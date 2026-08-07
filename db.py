"""
Camada de acesso a dados do Livro Caixa, conectando direto no PostgreSQL
do Supabase via psycopg2 puro (sem SQLAlchemy / st.connection).

Usa a string de conexão do CONNECTION POOLER (Session mode) do Supabase,
não a "Direct Connection" — a conexão direta hoje é IPv6-only, e
plataformas como o Streamlit Community Cloud costumam ter saída IPv6
instável.

Duas coisas que o psycopg2 puro não resolve sozinho, e que tratamos
explicitamente aqui:
  * A conexão é cacheada com @st.cache_resource — sem isso, cada rerun
    do Streamlit abriria uma conexão TCP nova, e o Session Pooler tem
    limite de conexões simultâneas.
  * Antes de devolver a conexão cacheada, um SELECT 1 confirma que ela
    ainda está viva — o pooler pode derrubar conexões ociosas, e sem
    essa checagem toda operação seguinte quebraria até reiniciar o app.

Todo write (INSERT/UPDATE/DELETE) faz rollback explícito em caso de erro
de integridade antes de relançar a exceção — sem isso, a conexão
cacheada fica numa transação "abortada" e toda operação seguinte falha,
mesmo sem relação nenhuma com o erro original.
"""

from __future__ import annotations

import psycopg2
import streamlit as st

def _conectar():
    # Injetando os identificadores oficiais exigidos pelo novo Pooler do Supabase
    return psycopg2.connect(
        host="aws-0-sa-east-1.pooler.supabase.com",
        database="postgres",
        user="postgres.uyqthqevmajrdctdpcuc",
        password="dessantcaixalivro",
        port=6543,
        sslmode="require"
    )

def get_connection():
    return _conectar()



@st.cache_resource
def _conectar():
    # =====================================================================
    # DIAGNÓSTICO TEMPORÁRIO — REMOVER DEPOIS DE RESOLVER O ERRO DE CONEXÃO.
    # Mostra a mensagem real do psycopg2/Postgres na tela (o Streamlit Cloud
    # esconde detalhes de exceção não tratada por padrão). Depois que a
    # conexão estiver funcionando, tire este try/except e volte a deixar
    # a função levantar a exceção normalmente — ver versão original abaixo.
    # =====================================================================
    try:
        return psycopg2.connect(
            host=st.secrets["connections"]["postgresql"]["host"],
            database=st.secrets["connections"]["postgresql"]["database"],
            user=st.secrets["connections"]["postgresql"]["user"],
            password=st.secrets["connections"]["postgresql"]["password"],
            port=st.secrets["connections"]["postgresql"]["port"],
            sslmode="require",
        )
    except Exception as e:
        st.error(f"Falha ao conectar no banco:\n\n{e}")
        st.stop()
    # ===================== FIM DO DIAGNÓSTICO TEMPORÁRIO =====================


def get_connection():
    """
    Devolve a conexão cacheada, reconectando automaticamente se o
    pooler tiver derrubado a conexão por inatividade.
    """
    conn = _conectar()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        conn.rollback()  # descarta a transação implícita do ping acima
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        _conectar.clear()
        conn = _conectar()
    return conn


def _consultar(conn, sql: str, params: dict | None = None) -> list[dict]:
    """SELECT genérico — devolve lista de dicts (uma linha = um dict)."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params or {})
        linhas = [dict(r) for r in cur.fetchall()]
    conn.rollback()  # SELECT não precisa de commit; fecha a transação implícita
    return linhas


def _executar_escrita(conn, sql: str, params: dict) -> None:
    """
    Executa um INSERT/UPDATE/DELETE. Em caso de erro de integridade
    (unique, FK, check), faz rollback explícito antes de relançar —
    essencial porque a conexão é reaproveitada entre reruns.
    """
    with conn.cursor() as cur:
        try:
            cur.execute(sql, params)
            conn.commit()
        except psycopg2.IntegrityError:
            conn.rollback()
            raise


def _normalizar(
    linhas: list[dict],
    colunas_numericas: tuple[str, ...] = (),
    colunas_data: tuple[str, ...] = (),
) -> list[dict]:
    """
    psycopg2 devolve NUMERIC como Decimal e DATE como datetime.date —
    convertemos pra float e string 'YYYY-MM-DD' respectivamente, que é
    o formato que o app.py espera (evita 'Decimal + float' explodindo
    em runtime, e date.fromisoformat() falhando no formulário de edição).
    """
    for linha in linhas:
        for col in colunas_numericas:
            if linha.get(col) is not None:
                linha[col] = float(linha[col])
        for col in colunas_data:
            if linha.get(col) is not None:
                linha[col] = str(linha[col])[:10]
    return linhas


# ---------------------------------------------------------------------
# Clientes
# ---------------------------------------------------------------------

def listar_clientes(conn):
    return _consultar(
        conn,
        "SELECT id_cliente, nome, cpf, telefone, email, observacoes "
        "FROM clientes ORDER BY nome",
    )


def inserir_cliente(conn, nome, cpf, telefone, email, observacoes) -> None:
    _executar_escrita(
        conn,
        """
        INSERT INTO clientes (nome, cpf, telefone, email, observacoes)
        VALUES (%(nome)s, %(cpf)s, %(telefone)s, %(email)s, %(observacoes)s)
        """,
        {"nome": nome, "cpf": cpf, "telefone": telefone, "email": email, "observacoes": observacoes},
    )


def buscar_cliente_por_id(conn, id_cliente: int):
    linhas = _consultar(
        conn,
        "SELECT id_cliente, nome, cpf, telefone, email, observacoes "
        "FROM clientes WHERE id_cliente = %(id)s",
        {"id": id_cliente},
    )
    return linhas[0] if linhas else None


def atualizar_cliente(conn, id_cliente, nome, cpf, telefone, email, observacoes) -> None:
    _executar_escrita(
        conn,
        """
        UPDATE clientes
        SET nome = %(nome)s, cpf = %(cpf)s, telefone = %(telefone)s,
            email = %(email)s, observacoes = %(observacoes)s
        WHERE id_cliente = %(id)s
        """,
        {"id": id_cliente, "nome": nome, "cpf": cpf, "telefone": telefone,
         "email": email, "observacoes": observacoes},
    )


def excluir_cliente(conn, id_cliente: int) -> None:
    """Levanta psycopg2.IntegrityError se houver Lançamentos ou
    Contas_Pagar_Receber vinculados a este cliente."""
    _executar_escrita(conn, "DELETE FROM clientes WHERE id_cliente = %(id)s", {"id": id_cliente})


# ---------------------------------------------------------------------
# Plano de Contas
# ---------------------------------------------------------------------

def listar_todas_contas(conn):
    return _consultar(
        conn,
        "SELECT id_conta, codigo, nome, id_pai, natureza FROM plano_contas ORDER BY codigo",
    )


def listar_plano_contas_folhas(conn):
    """Contas sem filhos — só elas podem receber lançamento."""
    return _consultar(
        conn,
        """
        SELECT id_conta, codigo, nome
        FROM plano_contas
        WHERE id_conta NOT IN (
            SELECT DISTINCT id_pai FROM plano_contas WHERE id_pai IS NOT NULL
        )
        ORDER BY codigo
        """,
    )


def buscar_conta_por_id(conn, id_conta: int):
    linhas = _consultar(
        conn,
        "SELECT id_conta, codigo, nome, id_pai, natureza FROM plano_contas WHERE id_conta = %(id)s",
        {"id": id_conta},
    )
    return linhas[0] if linhas else None


def _proximo_codigo(conn, id_pai) -> str:
    if id_pai is None:
        linhas = _consultar(conn, "SELECT codigo FROM plano_contas WHERE id_pai IS NULL")
        numeros = [int(r["codigo"]) for r in linhas if r["codigo"].isdigit()]
        return str(max(numeros, default=0) + 1)

    pai = _consultar(conn, "SELECT codigo FROM plano_contas WHERE id_conta = %(id)s", {"id": id_pai})
    codigo_pai = pai[0]["codigo"]

    filhos = _consultar(conn, "SELECT codigo FROM plano_contas WHERE id_pai = %(id_pai)s", {"id_pai": id_pai})
    sufixos = [int(f["codigo"].split(".")[-1]) for f in filhos if f["codigo"].split(".")[-1].isdigit()]
    return f"{codigo_pai}.{max(sufixos, default=0) + 1}"


def inserir_conta(conn, nome: str, id_pai, natureza: str) -> None:
    codigo = _proximo_codigo(conn, id_pai)
    _executar_escrita(
        conn,
        "INSERT INTO plano_contas (codigo, nome, id_pai, natureza) "
        "VALUES (%(codigo)s, %(nome)s, %(id_pai)s, %(natureza)s)",
        {"codigo": codigo, "nome": nome, "id_pai": id_pai, "natureza": natureza},
    )


def excluir_conta(conn, id_conta: int) -> None:
    """Levanta psycopg2.IntegrityError se a conta tiver subcontas ou
    lançamentos/contas a pagar vinculados — sem cascata."""
    _executar_escrita(conn, "DELETE FROM plano_contas WHERE id_conta = %(id)s", {"id": id_conta})


# ---------------------------------------------------------------------
# Lançamentos
# ---------------------------------------------------------------------

def inserir_lancamento(
    conn, data, tipo, valor, forma_pagamento, descricao, id_cliente, id_conta_plano,
    id_transacao_banco=None,
) -> None:
    _executar_escrita(
        conn,
        """
        INSERT INTO lancamentos
            (data, tipo, valor, forma_pagamento, descricao,
             id_cliente, id_conta_plano, id_transacao_banco)
        VALUES
            (%(data)s, %(tipo)s, %(valor)s, %(forma_pagamento)s, %(descricao)s,
             %(id_cliente)s, %(id_conta_plano)s, %(id_transacao_banco)s)
        """,
        {"data": data, "tipo": tipo, "valor": valor, "forma_pagamento": forma_pagamento,
         "descricao": descricao, "id_cliente": id_cliente, "id_conta_plano": id_conta_plano,
         "id_transacao_banco": id_transacao_banco},
    )


def listar_lancamentos(conn):
    linhas = _consultar(
        conn,
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
        FROM lancamentos l
        LEFT JOIN clientes c ON l.id_cliente = c.id_cliente
        JOIN plano_contas pc ON l.id_conta_plano = pc.id_conta
        ORDER BY l.data DESC, l.id_lancamento DESC
        """,
    )
    return _normalizar(linhas, colunas_numericas=("valor",), colunas_data=("data",))


def buscar_lancamento_por_id(conn, id_lancamento: int):
    linhas = _consultar(
        conn,
        """
        SELECT id_lancamento, data, tipo, valor, forma_pagamento, descricao,
               id_cliente, id_conta_plano, id_transacao_banco
        FROM lancamentos WHERE id_lancamento = %(id)s
        """,
        {"id": id_lancamento},
    )
    linhas = _normalizar(linhas, colunas_numericas=("valor",), colunas_data=("data",))
    return linhas[0] if linhas else None


def atualizar_lancamento(
    conn, id_lancamento, data, tipo, valor, forma_pagamento, descricao, id_cliente, id_conta_plano,
) -> None:
    _executar_escrita(
        conn,
        """
        UPDATE lancamentos
        SET data = %(data)s, tipo = %(tipo)s, valor = %(valor)s, forma_pagamento = %(forma_pagamento)s,
            descricao = %(descricao)s, id_cliente = %(id_cliente)s, id_conta_plano = %(id_conta_plano)s
        WHERE id_lancamento = %(id)s
        """,
        {"id": id_lancamento, "data": data, "tipo": tipo, "valor": valor,
         "forma_pagamento": forma_pagamento, "descricao": descricao,
         "id_cliente": id_cliente, "id_conta_plano": id_conta_plano},
    )


def excluir_lancamento(conn, id_lancamento: int) -> None:
    _executar_escrita(conn, "DELETE FROM lancamentos WHERE id_lancamento = %(id)s", {"id": id_lancamento})


# ---------------------------------------------------------------------
# DRE / Dashboard — CTE recursiva no banco (conexão SQL de verdade)
# ---------------------------------------------------------------------

_CTE_RAIZ = """
    WITH RECURSIVE raiz AS (
        SELECT id_conta AS id_conta_original, id_conta, codigo, nome, id_pai
        FROM plano_contas
        UNION ALL
        SELECT r.id_conta_original, pc.id_conta, pc.codigo, pc.nome, pc.id_pai
        FROM raiz r
        JOIN plano_contas pc ON pc.id_conta = r.id_pai
    ),
    raiz_final AS (
        SELECT id_conta_original, codigo, nome
        FROM raiz
        WHERE id_pai IS NULL
    )
"""


def buscar_totais_dre(conn):
    linhas = _consultar(
        conn,
        _CTE_RAIZ
        + """
        SELECT
            to_char(l.data, 'YYYY-MM') AS ano_mes,
            rf.codigo AS raiz_codigo,
            rf.nome AS raiz_nome,
            SUM(l.valor) AS total
        FROM lancamentos l
        JOIN raiz_final rf ON rf.id_conta_original = l.id_conta_plano
        GROUP BY ano_mes, rf.codigo, rf.nome
        ORDER BY ano_mes, rf.codigo
        """,
    )
    return _normalizar(linhas, colunas_numericas=("total",))


def listar_meses_disponiveis(conn):
    linhas = _consultar(
        conn,
        "SELECT DISTINCT to_char(data, 'YYYY-MM') AS ano_mes FROM lancamentos ORDER BY 1",
    )
    return [l["ano_mes"] for l in linhas]


def buscar_despesas_por_categoria(conn, ano_mes: str):
    linhas = _consultar(
        conn,
        _CTE_RAIZ
        + """
        SELECT
            (pc.codigo || ' - ' || pc.nome) AS categoria,
            SUM(l.valor) AS total
        FROM lancamentos l
        JOIN plano_contas pc ON pc.id_conta = l.id_conta_plano
        JOIN raiz_final rf ON rf.id_conta_original = l.id_conta_plano
        WHERE to_char(l.data, 'YYYY-MM') = %(ano_mes)s
          AND rf.codigo != '1'
        GROUP BY pc.id_conta, pc.codigo, pc.nome
        ORDER BY total DESC
        """,
        {"ano_mes": ano_mes},
    )
    return _normalizar(linhas, colunas_numericas=("total",))
