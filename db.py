"""
Camada de acesso a dados do Livro Caixa, conectando direto no PostgreSQL
do Supabase via SQLAlchemy (conector nativo st.connection(..., type="sql")).

Usa a string de conexão do CONNECTION POOLER (Session mode) do Supabase,
não a "Direct Connection" — a conexão direta hoje é IPv6-only, e
plataformas como o Streamlit Community Cloud costumam ter saída IPv6
instável, o que causa exatamente falhas de conexão intermitentes.

Todo write (INSERT/UPDATE/DELETE) faz rollback explícito em caso de erro
de integridade antes de relançar a exceção — sem isso, a sessão do
SQLAlchemy (que o Streamlit reaproveita entre reruns) fica numa
transação "abortada" e toda operação seguinte falha até reiniciar o app,
mesmo sem relação nenhuma com o erro original.
"""

from __future__ import annotations

import streamlit as st
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError  # noqa: F401 (reexportado pro app.py)


def get_connection():
    return st.connection("postgresql", type="sql")


def _executar_escrita(conn, sql: str, params: dict) -> None:
    """
    Executa um INSERT/UPDATE/DELETE. Em caso de erro de integridade
    (unique, FK, check), faz rollback explícito antes de relançar —
    essencial porque a sessão é reaproveitada entre reruns do Streamlit.
    """
    with conn.session as session:
        try:
            session.execute(text(sql), params)
            session.commit()
        except IntegrityError:
            session.rollback()
            raise


def _para_registros(df, colunas_numericas: tuple[str, ...] = (), colunas_data: tuple[str, ...] = ()) -> list[dict]:
    """
    Converte o DataFrame que conn.query() devolve numa lista de dicts
    simples, normalizando dois pontos que mudam de comportamento em
    relação à versão anterior (REST/JSON):
      * valores NUMERIC podem voltar como Decimal — forço float, senão
        'Decimal + float' explode em runtime na hora de somar totais.
      * colunas DATE podem voltar como Timestamp do pandas — forço
        string 'YYYY-MM-DD', que é o formato que o app.py espera
        (ex: date.fromisoformat() no formulário de edição).
    """
    for col in colunas_numericas:
        if col in df.columns:
            df[col] = df[col].astype(float)
    for col in colunas_data:
        if col in df.columns:
            df[col] = df[col].astype(str).str.slice(0, 10)
    return df.to_dict("records")


# ---------------------------------------------------------------------
# Clientes
# ---------------------------------------------------------------------

def listar_clientes(conn):
    df = conn.query(
        "SELECT id_cliente, nome, cpf, telefone, email, observacoes "
        "FROM clientes ORDER BY nome",
        ttl=0,
    )
    return _para_registros(df)


def inserir_cliente(conn, nome, cpf, telefone, email, observacoes) -> None:
    _executar_escrita(
        conn,
        """
        INSERT INTO clientes (nome, cpf, telefone, email, observacoes)
        VALUES (:nome, :cpf, :telefone, :email, :observacoes)
        """,
        {"nome": nome, "cpf": cpf, "telefone": telefone, "email": email, "observacoes": observacoes},
    )


def buscar_cliente_por_id(conn, id_cliente: int):
    df = conn.query(
        "SELECT id_cliente, nome, cpf, telefone, email, observacoes "
        "FROM clientes WHERE id_cliente = :id",
        params={"id": id_cliente},
        ttl=0,
    )
    registros = _para_registros(df)
    return registros[0] if registros else None


def atualizar_cliente(conn, id_cliente, nome, cpf, telefone, email, observacoes) -> None:
    _executar_escrita(
        conn,
        """
        UPDATE clientes
        SET nome = :nome, cpf = :cpf, telefone = :telefone,
            email = :email, observacoes = :observacoes
        WHERE id_cliente = :id
        """,
        {"id": id_cliente, "nome": nome, "cpf": cpf, "telefone": telefone,
         "email": email, "observacoes": observacoes},
    )


def excluir_cliente(conn, id_cliente: int) -> None:
    """Levanta sqlalchemy.exc.IntegrityError se houver Lançamentos ou
    Contas_Pagar_Receber vinculados a este cliente."""
    _executar_escrita(conn, "DELETE FROM clientes WHERE id_cliente = :id", {"id": id_cliente})


# ---------------------------------------------------------------------
# Plano de Contas
# ---------------------------------------------------------------------

def listar_todas_contas(conn):
    df = conn.query(
        "SELECT id_conta, codigo, nome, id_pai, natureza FROM plano_contas ORDER BY codigo",
        ttl=0,
    )
    return _para_registros(df)


def listar_plano_contas_folhas(conn):
    """Contas sem filhos — só elas podem receber lançamento."""
    df = conn.query(
        """
        SELECT id_conta, codigo, nome
        FROM plano_contas
        WHERE id_conta NOT IN (
            SELECT DISTINCT id_pai FROM plano_contas WHERE id_pai IS NOT NULL
        )
        ORDER BY codigo
        """,
        ttl=0,
    )
    return _para_registros(df)


def buscar_conta_por_id(conn, id_conta: int):
    df = conn.query(
        "SELECT id_conta, codigo, nome, id_pai, natureza FROM plano_contas WHERE id_conta = :id",
        params={"id": id_conta},
        ttl=0,
    )
    registros = _para_registros(df)
    return registros[0] if registros else None


def _proximo_codigo(conn, id_pai) -> str:
    if id_pai is None:
        df = conn.query(
            "SELECT codigo FROM plano_contas WHERE id_pai IS NULL", ttl=0
        )
        numeros = [int(c) for c in df["codigo"] if str(c).isdigit()]
        return str(max(numeros, default=0) + 1)

    df_pai = conn.query(
        "SELECT codigo FROM plano_contas WHERE id_conta = :id",
        params={"id": id_pai},
        ttl=0,
    )
    codigo_pai = df_pai["codigo"].iloc[0]

    df_filhos = conn.query(
        "SELECT codigo FROM plano_contas WHERE id_pai = :id_pai",
        params={"id_pai": id_pai},
        ttl=0,
    )
    sufixos = [
        int(c.split(".")[-1]) for c in df_filhos["codigo"] if c.split(".")[-1].isdigit()
    ]
    return f"{codigo_pai}.{max(sufixos, default=0) + 1}"


def inserir_conta(conn, nome: str, id_pai, natureza: str) -> None:
    codigo = _proximo_codigo(conn, id_pai)
    _executar_escrita(
        conn,
        "INSERT INTO plano_contas (codigo, nome, id_pai, natureza) VALUES (:codigo, :nome, :id_pai, :natureza)",
        {"codigo": codigo, "nome": nome, "id_pai": id_pai, "natureza": natureza},
    )


def excluir_conta(conn, id_conta: int) -> None:
    """Levanta sqlalchemy.exc.IntegrityError se a conta tiver subcontas
    ou lançamentos/contas a pagar vinculados — sem cascata."""
    _executar_escrita(conn, "DELETE FROM plano_contas WHERE id_conta = :id", {"id": id_conta})


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
            (:data, :tipo, :valor, :forma_pagamento, :descricao,
             :id_cliente, :id_conta_plano, :id_transacao_banco)
        """,
        {"data": data, "tipo": tipo, "valor": valor, "forma_pagamento": forma_pagamento,
         "descricao": descricao, "id_cliente": id_cliente, "id_conta_plano": id_conta_plano,
         "id_transacao_banco": id_transacao_banco},
    )


def listar_lancamentos(conn):
    df = conn.query(
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
        ttl=0,
    )
    return _para_registros(df, colunas_numericas=("valor",), colunas_data=("data",))


def buscar_lancamento_por_id(conn, id_lancamento: int):
    df = conn.query(
        """
        SELECT id_lancamento, data, tipo, valor, forma_pagamento, descricao,
               id_cliente, id_conta_plano, id_transacao_banco
        FROM lancamentos WHERE id_lancamento = :id
        """,
        params={"id": id_lancamento},
        ttl=0,
    )
    registros = _para_registros(df, colunas_numericas=("valor",), colunas_data=("data",))
    return registros[0] if registros else None


def atualizar_lancamento(
    conn, id_lancamento, data, tipo, valor, forma_pagamento, descricao, id_cliente, id_conta_plano,
) -> None:
    _executar_escrita(
        conn,
        """
        UPDATE lancamentos
        SET data = :data, tipo = :tipo, valor = :valor, forma_pagamento = :forma_pagamento,
            descricao = :descricao, id_cliente = :id_cliente, id_conta_plano = :id_conta_plano
        WHERE id_lancamento = :id
        """,
        {"id": id_lancamento, "data": data, "tipo": tipo, "valor": valor,
         "forma_pagamento": forma_pagamento, "descricao": descricao,
         "id_cliente": id_cliente, "id_conta_plano": id_conta_plano},
    )


def excluir_lancamento(conn, id_lancamento: int) -> None:
    _executar_escrita(conn, "DELETE FROM lancamentos WHERE id_lancamento = :id", {"id": id_lancamento})


# ---------------------------------------------------------------------
# DRE / Dashboard
# Com conexão SQL de verdade, voltamos a usar CTE recursiva no banco
# (mais simples e mais rápido que refazer a árvore em Python, que foi
# o contorno necessário na versão anterior via API REST).
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
    df = conn.query(
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
        ttl=0,
    )
    return _para_registros(df, colunas_numericas=("total",))


def listar_meses_disponiveis(conn):
    df = conn.query(
        "SELECT DISTINCT to_char(data, 'YYYY-MM') AS ano_mes FROM lancamentos ORDER BY ano_mes",
        ttl=0,
    )
    return df["ano_mes"].tolist()


def buscar_despesas_por_categoria(conn, ano_mes: str):
    df = conn.query(
        _CTE_RAIZ
        + """
        SELECT
            (pc.codigo || ' - ' || pc.nome) AS categoria,
            SUM(l.valor) AS total
        FROM lancamentos l
        JOIN plano_contas pc ON pc.id_conta = l.id_conta_plano
        JOIN raiz_final rf ON rf.id_conta_original = l.id_conta_plano
        WHERE to_char(l.data, 'YYYY-MM') = :ano_mes
          AND rf.codigo != '1'
        GROUP BY pc.id_conta, pc.codigo, pc.nome
        ORDER BY total DESC
        """,
        params={"ano_mes": ano_mes},
        ttl=0,
    )
    return _para_registros(df, colunas_numericas=("total",))
