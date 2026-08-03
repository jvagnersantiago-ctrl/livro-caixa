"""
Camada de acesso ao banco de dados livro_caixa.db.
Mantém toda a lógica de SQL separada da interface Streamlit.
"""

import sqlite3

DB_PATH = "livro_caixa.db"


def get_connection() -> sqlite3.Connection:
    """
    Abre uma conexão com o banco. check_same_thread=False é necessário
    porque o Streamlit pode acessar a conexão em threads diferentes
    entre reruns do app.
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------
# Clientes
# ---------------------------------------------------------------------

def listar_clientes(conn: sqlite3.Connection):
    return conn.execute(
        """
        SELECT id_cliente, nome, cpf, telefone, email, observacoes
        FROM Clientes
        ORDER BY nome
        """
    ).fetchall()


def inserir_cliente(conn, nome, cpf, telefone, email, observacoes) -> None:
    conn.execute(
        """
        INSERT INTO Clientes (nome, cpf, telefone, email, observacoes)
        VALUES (?, ?, ?, ?, ?)
        """,
        (nome, cpf, telefone, email, observacoes),
    )
    conn.commit()


def buscar_cliente_por_id(conn: sqlite3.Connection, id_cliente: int):
    return conn.execute(
        """
        SELECT id_cliente, nome, cpf, telefone, email, observacoes
        FROM Clientes
        WHERE id_cliente = ?
        """,
        (id_cliente,),
    ).fetchone()


def atualizar_cliente(conn, id_cliente, nome, cpf, telefone, email, observacoes) -> None:
    conn.execute(
        """
        UPDATE Clientes
        SET nome = ?, cpf = ?, telefone = ?, email = ?, observacoes = ?
        WHERE id_cliente = ?
        """,
        (nome, cpf, telefone, email, observacoes, id_cliente),
    )
    conn.commit()


def excluir_cliente(conn: sqlite3.Connection, id_cliente: int) -> None:
    """
    Levanta sqlite3.IntegrityError se houver Lancamentos ou
    Contas_Pagar_Receber vinculados a este cliente — a FK bloqueia de
    propósito, não fazemos SET NULL automático nem cascata.
    """
    conn.execute("DELETE FROM Clientes WHERE id_cliente = ?", (id_cliente,))
    conn.commit()


# ---------------------------------------------------------------------
# Plano de Contas
# ---------------------------------------------------------------------

def listar_plano_contas_folhas(conn: sqlite3.Connection):
    """
    Retorna apenas as contas 'folha' (sem filhos) do Plano de Contas.
    Lançamentos só devem ser vinculados a contas-folha — vincular a uma
    conta-pai quebra a soma hierárquica usada em relatórios.
    """
    return conn.execute(
        """
        SELECT id_conta, codigo, nome
        FROM Plano_Contas
        WHERE id_conta NOT IN (
            SELECT DISTINCT id_pai FROM Plano_Contas WHERE id_pai IS NOT NULL
        )
        ORDER BY codigo
        """
    ).fetchall()


def listar_todas_contas(conn: sqlite3.Connection):
    """Todas as contas (pais e folhas), usado pra montar a árvore e os
    seletores de conta-pai no Gerenciador do Plano de Contas."""
    return conn.execute(
        """
        SELECT id_conta, codigo, nome, id_pai, natureza
        FROM Plano_Contas
        ORDER BY codigo
        """
    ).fetchall()


def buscar_conta_por_id(conn: sqlite3.Connection, id_conta: int):
    return conn.execute(
        "SELECT id_conta, codigo, nome, id_pai, natureza FROM Plano_Contas WHERE id_conta = ?",
        (id_conta,),
    ).fetchone()


def _proximo_codigo(conn: sqlite3.Connection, id_pai) -> str:
    """
    Calcula o próximo código disponível pra uma nova conta.
    Conta raiz (sem pai): próximo inteiro livre ('1','2','3'...).
    Subconta: código do pai + '.' + próximo sufixo livre (ex: '2.5').
    O código é só rótulo de exibição — quem garante a hierarquia de
    verdade é sempre id_pai, nunca esse texto.
    """
    if id_pai is None:
        maiores = conn.execute(
            "SELECT codigo FROM Plano_Contas WHERE id_pai IS NULL"
        ).fetchall()
        numeros = [int(r["codigo"]) for r in maiores if r["codigo"].isdigit()]
        proximo = max(numeros, default=0) + 1
        return str(proximo)

    pai = conn.execute(
        "SELECT codigo FROM Plano_Contas WHERE id_conta = ?", (id_pai,)
    ).fetchone()
    filhos = conn.execute(
        "SELECT codigo FROM Plano_Contas WHERE id_pai = ?", (id_pai,)
    ).fetchall()
    sufixos = []
    for f in filhos:
        partes = f["codigo"].split(".")
        if partes[-1].isdigit():
            sufixos.append(int(partes[-1]))
    proximo_sufixo = max(sufixos, default=0) + 1
    return f"{pai['codigo']}.{proximo_sufixo}"


def inserir_conta(conn: sqlite3.Connection, nome: str, id_pai, natureza: str) -> None:
    codigo = _proximo_codigo(conn, id_pai)
    conn.execute(
        """
        INSERT INTO Plano_Contas (codigo, nome, id_pai, natureza)
        VALUES (?, ?, ?, ?)
        """,
        (codigo, nome, id_pai, natureza),
    )
    conn.commit()


def excluir_conta(conn: sqlite3.Connection, id_conta: int) -> None:
    """
    Levanta sqlite3.IntegrityError se a conta tiver subcontas ou
    Lançamentos/Contas_Pagar_Receber vinculados — de propósito, sem
    cascata: apagar uma categoria não deve apagar histórico financeiro.
    """
    conn.execute("DELETE FROM Plano_Contas WHERE id_conta = ?", (id_conta,))
    conn.commit()


# ---------------------------------------------------------------------
# Lançamentos
# ---------------------------------------------------------------------

def inserir_lancamento(
    conn,
    data,
    tipo,
    valor,
    forma_pagamento,
    descricao,
    id_cliente,
    id_conta_plano,
    id_transacao_banco=None,
) -> None:
    conn.execute(
        """
        INSERT INTO Lancamentos
            (data, tipo, valor, forma_pagamento, descricao,
             id_cliente, id_conta_plano, id_transacao_banco)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            data,
            tipo,
            valor,
            forma_pagamento,
            descricao,
            id_cliente,
            id_conta_plano,
            id_transacao_banco,
        ),
    )
    conn.commit()


def listar_lancamentos(conn: sqlite3.Connection):
    return conn.execute(
        """
        SELECT
            l.id_lancamento AS id,
            l.data,
            l.tipo,
            l.valor,
            l.forma_pagamento,
            l.descricao,
            c.nome AS cliente,
            (pc.codigo || ' - ' || pc.nome) AS categoria
        FROM Lancamentos l
        LEFT JOIN Clientes c ON l.id_cliente = c.id_cliente
        JOIN Plano_Contas pc ON l.id_conta_plano = pc.id_conta
        ORDER BY l.data DESC, l.id_lancamento DESC
        """
    ).fetchall()


def buscar_lancamento_por_id(conn: sqlite3.Connection, id_lancamento: int):
    """
    Retorna a linha 'crua' do lançamento (com os IDs de FK, não os nomes
    já resolvidos) — é isso que o formulário de edição precisa pra
    pré-selecionar os dropdowns corretamente.
    """
    return conn.execute(
        """
        SELECT id_lancamento, data, tipo, valor, forma_pagamento, descricao,
               id_cliente, id_conta_plano, id_transacao_banco
        FROM Lancamentos
        WHERE id_lancamento = ?
        """,
        (id_lancamento,),
    ).fetchone()


def atualizar_lancamento(
    conn,
    id_lancamento,
    data,
    tipo,
    valor,
    forma_pagamento,
    descricao,
    id_cliente,
    id_conta_plano,
) -> None:
    conn.execute(
        """
        UPDATE Lancamentos
        SET data = ?,
            tipo = ?,
            valor = ?,
            forma_pagamento = ?,
            descricao = ?,
            id_cliente = ?,
            id_conta_plano = ?
        WHERE id_lancamento = ?
        """,
        (
            data,
            tipo,
            valor,
            forma_pagamento,
            descricao,
            id_cliente,
            id_conta_plano,
            id_lancamento,
        ),
    )
    conn.commit()


def excluir_lancamento(conn: sqlite3.Connection, id_lancamento: int) -> None:
    conn.execute(
        "DELETE FROM Lancamentos WHERE id_lancamento = ?", (id_lancamento,)
    )
    conn.commit()


# ---------------------------------------------------------------------
# DRE (Demonstração do Resultado do Exercício)
# ---------------------------------------------------------------------

def buscar_totais_dre(conn: sqlite3.Connection):
    """
    Agrega todos os Lançamentos por Ano/Mês e pela categoria RAIZ do
    Plano de Contas (ex: '1. Receitas', '2. Despesas Fixas', etc.),
    não pela categoria-folha em que o lançamento foi de fato registrado.

    Usa uma CTE recursiva pra subir a árvore do Plano de Contas até
    encontrar o ancestral sem pai (id_pai IS NULL) — isso funciona
    independente de quantos níveis de profundidade existirem.

    Retorna linhas: ano_mes, raiz_codigo, raiz_nome, total
    """
    return conn.execute(
        """
        WITH RECURSIVE raiz AS (
            SELECT id_conta AS id_conta_original, id_conta, codigo, nome, id_pai
            FROM Plano_Contas
            UNION ALL
            SELECT r.id_conta_original, pc.id_conta, pc.codigo, pc.nome, pc.id_pai
            FROM raiz r
            JOIN Plano_Contas pc ON pc.id_conta = r.id_pai
        ),
        raiz_final AS (
            SELECT id_conta_original, codigo, nome
            FROM raiz
            WHERE id_pai IS NULL
        )
        SELECT
            strftime('%Y-%m', l.data) AS ano_mes,
            rf.codigo AS raiz_codigo,
            rf.nome AS raiz_nome,
            SUM(l.valor) AS total
        FROM Lancamentos l
        JOIN raiz_final rf ON rf.id_conta_original = l.id_conta_plano
        GROUP BY ano_mes, rf.codigo, rf.nome
        ORDER BY ano_mes, rf.codigo
        """
    ).fetchall()


def listar_meses_disponiveis(conn: sqlite3.Connection):
    """Lista os meses (formato 'YYYY-MM') que têm ao menos um lançamento."""
    return [
        row["ano_mes"]
        for row in conn.execute(
            """
            SELECT DISTINCT strftime('%Y-%m', data) AS ano_mes
            FROM Lancamentos
            ORDER BY ano_mes
            """
        ).fetchall()
    ]


def buscar_despesas_por_categoria(conn: sqlite3.Connection, ano_mes: str):
    """
    Detalha as despesas do mês informado pela categoria-folha real onde
    cada lançamento foi registrado (não pela raiz) — granularidade
    necessária pro gráfico de pizza mostrar onde o dinheiro está indo
    (ex: 'Aluguel' e 'Marketing' separados, não só 'Despesas').

    Inclui Impostos como despesa aqui (raiz != Receitas), decisão
    explicada no chat.
    """
    return conn.execute(
        """
        WITH RECURSIVE raiz AS (
            SELECT id_conta AS id_conta_original, id_conta, codigo, nome, id_pai
            FROM Plano_Contas
            UNION ALL
            SELECT r.id_conta_original, pc.id_conta, pc.codigo, pc.nome, pc.id_pai
            FROM raiz r
            JOIN Plano_Contas pc ON pc.id_conta = r.id_pai
        ),
        raiz_final AS (
            SELECT id_conta_original, codigo
            FROM raiz
            WHERE id_pai IS NULL
        )
        SELECT
            (pc.codigo || ' - ' || pc.nome) AS categoria,
            SUM(l.valor) AS total
        FROM Lancamentos l
        JOIN Plano_Contas pc ON pc.id_conta = l.id_conta_plano
        JOIN raiz_final rf ON rf.id_conta_original = l.id_conta_plano
        WHERE strftime('%Y-%m', l.data) = ?
          AND rf.codigo != '1'
        GROUP BY pc.id_conta
        ORDER BY total DESC
        """,
        (ano_mes,),
    ).fetchall()
