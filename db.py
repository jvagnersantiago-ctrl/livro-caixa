"""
Camada de acesso ao banco de dados livro_caixa.db.
Mantém toda a lógica de SQL separada da interface Streamlit.
"""

import sqlite3

DB_PATH = "livro_caixa.db"

# ---------------------------------------------------------------------
# Schema embutido (não depende de um arquivo .sql separado existir no
# mesmo diretório no servidor — reduz uma fonte de erro no deploy).
# Todo CREATE usa IF NOT EXISTS: rodar isso de novo num banco que já
# existe não apaga nem duplica nada.
# ---------------------------------------------------------------------
SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS Clientes (
    id_cliente      INTEGER PRIMARY KEY AUTOINCREMENT,
    nome            TEXT,
    cpf             TEXT UNIQUE,
    telefone        TEXT,
    email           TEXT,
    observacoes     TEXT
);

CREATE TABLE IF NOT EXISTS Plano_Contas (
    id_conta        INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo          TEXT NOT NULL UNIQUE,
    nome            TEXT NOT NULL,
    id_pai          INTEGER,
    natureza        TEXT NOT NULL CHECK (natureza IN ('Receita', 'Despesa')),
    FOREIGN KEY (id_pai) REFERENCES Plano_Contas(id_conta)
);

CREATE TABLE IF NOT EXISTS Lancamentos (
    id_lancamento       INTEGER PRIMARY KEY AUTOINCREMENT,
    data                TEXT NOT NULL,
    tipo                TEXT NOT NULL CHECK (tipo IN ('Entrada', 'Saída')),
    valor               REAL NOT NULL CHECK (valor > 0),
    forma_pagamento     TEXT,
    descricao           TEXT,
    id_cliente          INTEGER,
    id_conta_plano      INTEGER NOT NULL,
    id_transacao_banco  TEXT,
    criado_em           TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (id_cliente)     REFERENCES Clientes(id_cliente),
    FOREIGN KEY (id_conta_plano) REFERENCES Plano_Contas(id_conta)
);

CREATE INDEX IF NOT EXISTS idx_lancamentos_data     ON Lancamentos(data);
CREATE INDEX IF NOT EXISTS idx_lancamentos_conta    ON Lancamentos(id_conta_plano);
CREATE INDEX IF NOT EXISTS idx_lancamentos_cliente  ON Lancamentos(id_cliente);

CREATE TABLE IF NOT EXISTS Contas_Pagar_Receber (
    id_conta_pr     INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo            TEXT NOT NULL CHECK (tipo IN ('Pagar', 'Receber')),
    id_cliente      INTEGER,
    id_conta_plano  INTEGER NOT NULL,
    descricao       TEXT,
    valor           REAL NOT NULL CHECK (valor > 0),
    data_vencimento TEXT NOT NULL,
    data_pagamento  TEXT,
    status          TEXT NOT NULL DEFAULT 'Pendente' CHECK (status IN ('Pendente', 'Pago', 'Atrasado')),
    FOREIGN KEY (id_cliente)     REFERENCES Clientes(id_cliente),
    FOREIGN KEY (id_conta_plano) REFERENCES Plano_Contas(id_conta)
);

CREATE INDEX IF NOT EXISTS idx_cpr_vencimento ON Contas_Pagar_Receber(data_vencimento);
CREATE INDEX IF NOT EXISTS idx_cpr_status     ON Contas_Pagar_Receber(status);

CREATE VIEW IF NOT EXISTS vw_contas_pagar_receber_status_real AS
SELECT
    c.*,
    CASE
        WHEN c.status = 'Pago' THEN 'Pago'
        WHEN c.status = 'Pendente' AND date(c.data_vencimento) < date('now') THEN 'Atrasado'
        ELSE c.status
    END AS status_real
FROM Contas_Pagar_Receber c;
"""

# (codigo, nome, codigo_pai_ou_None, natureza) — pais sempre antes dos filhos.
PLANO_CONTAS_PADRAO = [
    ("1",   "Receitas",                    None, "Receita"),
    ("1.1", "Receitas de Serviços",        "1",  "Receita"),
    ("1.2", "Receitas de Vendas",          "1",  "Receita"),
    ("1.3", "Outras Receitas",             "1",  "Receita"),
    ("2",   "Despesas Fixas",              None, "Despesa"),
    ("2.1", "Aluguel",                     "2",  "Despesa"),
    ("2.2", "Salários e Pró-labore",       "2",  "Despesa"),
    ("2.3", "Contabilidade",               "2",  "Despesa"),
    ("2.4", "Assinaturas e Softwares",     "2",  "Despesa"),
    ("3",   "Despesas Variáveis",          None, "Despesa"),
    ("3.1", "Marketing e Publicidade",     "3",  "Despesa"),
    ("3.2", "Materiais e Insumos",         "3",  "Despesa"),
    ("3.3", "Comissões",                   "3",  "Despesa"),
    ("3.4", "Manutenção",                  "3",  "Despesa"),
    ("4",   "Impostos",                    None, "Despesa"),
    ("4.1", "DAS (Simples Nacional / MEI)", "4", "Despesa"),
    ("4.2", "ISS",                         "4",  "Despesa"),
    ("4.3", "IRPJ/CSLL",                   "4",  "Despesa"),
]


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


def inicializar_banco(conn: sqlite3.Connection) -> None:
    """
    Garante que a estrutura do banco existe. Idempotente e segura de
    chamar em toda inicialização do app (local ou na nuvem):
    - cria tabelas/índices/view só se ainda não existirem;
    - só insere o Plano de Contas padrão se a tabela estiver vazia
      (não duplica se já tiver dados, inclusive contas customizadas
      que o usuário tenha criado no Gerenciador do Plano de Contas).
    """
    conn.executescript(SCHEMA_SQL)

    (qtd_contas,) = conn.execute("SELECT COUNT(*) FROM Plano_Contas").fetchone()
    if qtd_contas == 0:
        codigo_para_id: dict[str, int] = {}
        cur = conn.cursor()
        for codigo, nome, codigo_pai, natureza in PLANO_CONTAS_PADRAO:
            id_pai = codigo_para_id.get(codigo_pai) if codigo_pai else None
            cur.execute(
                "INSERT INTO Plano_Contas (codigo, nome, id_pai, natureza) VALUES (?, ?, ?, ?)",
                (codigo, nome, id_pai, natureza),
            )
            codigo_para_id[codigo] = cur.lastrowid
        conn.commit()


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
