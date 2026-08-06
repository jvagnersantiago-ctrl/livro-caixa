"""
Camada de acesso a dados do Livro Caixa, agora falando com o Supabase
(PostgreSQL) via API REST (postgrest), através da lib st-supabase-connection.

Diferença importante em relação à versão SQLite: não existe SQL arbitrário
aqui (a API REST não aceita isso). Consultas que antes eram uma CTE
recursiva ou usavam strftime() agora buscam os dados brutos e fazem a
agregação em Python — o Plano de Contas inteiro cabe tranquilamente em
memória (poucas dezenas de linhas), então isso não é gargalo de
performance, é só uma forma diferente de resolver o mesmo problema.

Todas as leituras usam ttl=0 (cache desligado) de propósito: um
lançamento que acabou de ser gravado precisa aparecer imediatamente no
histórico, na DRE e no Dashboard — cache de resultado atrapalharia isso.
"""

from __future__ import annotations

import streamlit as st
from postgrest import APIError  # noqa: F401 (reexportado pra uso no app.py)
from st_supabase_connection import SupabaseConnection, execute_query


def get_connection():
    """
    st.connection já cacheia o client em si (não a query) — chamar isso
    de novo em todo rerun do Streamlit é barato e seguro.
    """
    return st.connection("supabase_connection", type=SupabaseConnection, ttl=None)


# ---------------------------------------------------------------------
# Clientes
# ---------------------------------------------------------------------

def listar_clientes(conn):
    resp = execute_query(
        conn.table("clientes")
        .select("id_cliente, nome, cpf, telefone, email, observacoes")
        .order("nome"),
        ttl=0,
    )
    return resp.data


def inserir_cliente(conn, nome, cpf, telefone, email, observacoes) -> None:
    execute_query(
        conn.table("clientes").insert(
            {
                "nome": nome,
                "cpf": cpf,
                "telefone": telefone,
                "email": email,
                "observacoes": observacoes,
            }
        ),
        ttl=0,
    )


def buscar_cliente_por_id(conn, id_cliente: int):
    resp = execute_query(
        conn.table("clientes").select("*").eq("id_cliente", id_cliente), ttl=0
    )
    return resp.data[0] if resp.data else None


def atualizar_cliente(conn, id_cliente, nome, cpf, telefone, email, observacoes) -> None:
    execute_query(
        conn.table("clientes")
        .update(
            {
                "nome": nome,
                "cpf": cpf,
                "telefone": telefone,
                "email": email,
                "observacoes": observacoes,
            }
        )
        .eq("id_cliente", id_cliente),
        ttl=0,
    )


def excluir_cliente(conn, id_cliente: int) -> None:
    """Levanta postgrest.APIError (code 23503) se houver Lançamentos ou
    Contas_Pagar_Receber vinculados a este cliente — sem SET NULL nem
    cascata, de propósito."""
    execute_query(conn.table("clientes").delete().eq("id_cliente", id_cliente), ttl=0)


# ---------------------------------------------------------------------
# Plano de Contas
# ---------------------------------------------------------------------

def listar_todas_contas(conn):
    resp = execute_query(
        conn.table("plano_contas")
        .select("id_conta, codigo, nome, id_pai, natureza")
        .order("codigo"),
        ttl=0,
    )
    return resp.data


def listar_plano_contas_folhas(conn):
    """Contas sem filhos — só elas podem receber lançamento."""
    todas = listar_todas_contas(conn)
    ids_com_filhos = {c["id_pai"] for c in todas if c["id_pai"] is not None}
    folhas = [c for c in todas if c["id_conta"] not in ids_com_filhos]
    folhas.sort(key=lambda c: c["codigo"])
    return folhas


def buscar_conta_por_id(conn, id_conta: int):
    resp = execute_query(
        conn.table("plano_contas").select("*").eq("id_conta", id_conta), ttl=0
    )
    return resp.data[0] if resp.data else None


def _proximo_codigo(conn, id_pai) -> str:
    if id_pai is None:
        resp = execute_query(
            conn.table("plano_contas").select("codigo").is_("id_pai", "null"), ttl=0
        )
        numeros = [int(r["codigo"]) for r in resp.data if r["codigo"].isdigit()]
        return str(max(numeros, default=0) + 1)

    pai_resp = execute_query(
        conn.table("plano_contas").select("codigo").eq("id_conta", id_pai), ttl=0
    )
    codigo_pai = pai_resp.data[0]["codigo"]

    filhos_resp = execute_query(
        conn.table("plano_contas").select("codigo").eq("id_pai", id_pai), ttl=0
    )
    sufixos = [
        int(f["codigo"].split(".")[-1])
        for f in filhos_resp.data
        if f["codigo"].split(".")[-1].isdigit()
    ]
    return f"{codigo_pai}.{max(sufixos, default=0) + 1}"


def inserir_conta(conn, nome: str, id_pai, natureza: str) -> None:
    codigo = _proximo_codigo(conn, id_pai)
    execute_query(
        conn.table("plano_contas").insert(
            {"codigo": codigo, "nome": nome, "id_pai": id_pai, "natureza": natureza}
        ),
        ttl=0,
    )


def excluir_conta(conn, id_conta: int) -> None:
    """Levanta postgrest.APIError (code 23503) se a conta tiver
    subcontas ou lançamentos/contas a pagar vinculados — sem cascata."""
    execute_query(conn.table("plano_contas").delete().eq("id_conta", id_conta), ttl=0)


def _mapa_raiz(contas: list[dict]) -> dict[int, tuple[str, str]]:
    """
    Pra cada conta, sobe a árvore até achar o ancestral sem pai (raiz) e
    devolve {id_conta: (codigo_raiz, nome_raiz)}. Substitui a CTE
    recursiva que existia na versão SQLite — mesmo resultado, calculado
    em memória porque a API REST não aceita SQL recursivo.
    """
    por_id = {c["id_conta"]: c for c in contas}
    mapa: dict[int, tuple[str, str]] = {}
    for c in contas:
        atual = c
        visitados = set()
        while atual["id_pai"] is not None:
            if atual["id_conta"] in visitados:
                break  # proteção contra ciclo acidental no id_pai
            visitados.add(atual["id_conta"])
            atual = por_id[atual["id_pai"]]
        mapa[c["id_conta"]] = (atual["codigo"], atual["nome"])
    return mapa


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
    execute_query(
        conn.table("lancamentos").insert(
            {
                "data": data,
                "tipo": tipo,
                "valor": valor,
                "forma_pagamento": forma_pagamento,
                "descricao": descricao,
                "id_cliente": id_cliente,
                "id_conta_plano": id_conta_plano,
                "id_transacao_banco": id_transacao_banco,
            }
        ),
        ttl=0,
    )


def listar_lancamentos(conn):
    """
    Usa o embed automático de FK do PostgREST (clientes(nome),
    plano_contas(codigo,nome)) em vez de um JOIN manual, e depois achata
    o resultado pro mesmo formato plano que o app já espera.
    """
    resp = execute_query(
        conn.table("lancamentos")
        .select(
            "id_lancamento, data, tipo, valor, forma_pagamento, descricao, "
            "id_cliente, id_conta_plano, clientes(nome), plano_contas(codigo, nome)"
        )
        .order("data", desc=True)
        .order("id_lancamento", desc=True),
        ttl=0,
    )

    lancamentos = []
    for row in resp.data:
        cliente = row.get("clientes")
        plano = row.get("plano_contas")
        lancamentos.append(
            {
                "id": row["id_lancamento"],
                "data": row["data"],
                "tipo": row["tipo"],
                "valor": float(row["valor"]),
                "forma_pagamento": row.get("forma_pagamento"),
                "descricao": row.get("descricao"),
                "cliente": cliente["nome"] if cliente else None,
                "categoria": f"{plano['codigo']} - {plano['nome']}" if plano else "—",
            }
        )
    return lancamentos


def buscar_lancamento_por_id(conn, id_lancamento: int):
    resp = execute_query(
        conn.table("lancamentos")
        .select(
            "id_lancamento, data, tipo, valor, forma_pagamento, descricao, "
            "id_cliente, id_conta_plano, id_transacao_banco"
        )
        .eq("id_lancamento", id_lancamento),
        ttl=0,
    )
    return resp.data[0] if resp.data else None


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
    execute_query(
        conn.table("lancamentos")
        .update(
            {
                "data": data,
                "tipo": tipo,
                "valor": valor,
                "forma_pagamento": forma_pagamento,
                "descricao": descricao,
                "id_cliente": id_cliente,
                "id_conta_plano": id_conta_plano,
            }
        )
        .eq("id_lancamento", id_lancamento),
        ttl=0,
    )


def excluir_lancamento(conn, id_lancamento: int) -> None:
    execute_query(
        conn.table("lancamentos").delete().eq("id_lancamento", id_lancamento), ttl=0
    )


# ---------------------------------------------------------------------
# DRE / Dashboard — agregação em Python (ver _mapa_raiz acima)
# ---------------------------------------------------------------------

def _lancamentos_brutos(conn, ano_mes: str | None = None):
    query = conn.table("lancamentos").select("data, valor, id_conta_plano")
    if ano_mes:
        ano, mes = map(int, ano_mes.split("-"))
        proximo = f"{ano + 1}-01-01" if mes == 12 else f"{ano}-{mes + 1:02d}-01"
        query = query.gte("data", f"{ano_mes}-01").lt("data", proximo)
    resp = execute_query(query, ttl=0)
    return resp.data


def buscar_totais_dre(conn):
    contas = listar_todas_contas(conn)
    raiz_de = _mapa_raiz(contas)
    lancamentos = _lancamentos_brutos(conn)

    somas: dict[tuple[str, str, str], float] = {}
    for l in lancamentos:
        ano_mes = l["data"][:7]
        raiz_codigo, raiz_nome = raiz_de.get(l["id_conta_plano"], ("?", "Sem categoria"))
        chave = (ano_mes, raiz_codigo, raiz_nome)
        somas[chave] = somas.get(chave, 0.0) + float(l["valor"])

    linhas = [
        {"ano_mes": am, "raiz_codigo": rc, "raiz_nome": rn, "total": total}
        for (am, rc, rn), total in somas.items()
    ]
    linhas.sort(key=lambda r: (r["ano_mes"], r["raiz_codigo"]))
    return linhas


def listar_meses_disponiveis(conn):
    lancamentos = _lancamentos_brutos(conn)
    return sorted({l["data"][:7] for l in lancamentos})


def buscar_despesas_por_categoria(conn, ano_mes: str):
    contas = listar_todas_contas(conn)
    contas_por_id = {c["id_conta"]: c for c in contas}
    raiz_de = _mapa_raiz(contas)

    lancamentos = _lancamentos_brutos(conn, ano_mes=ano_mes)

    somas: dict[int, float] = {}
    for l in lancamentos:
        raiz_codigo, _ = raiz_de.get(l["id_conta_plano"], ("1", ""))
        if raiz_codigo == "1":
            continue  # exclui Receitas — só queremos despesas aqui
        somas[l["id_conta_plano"]] = somas.get(l["id_conta_plano"], 0.0) + float(l["valor"])

    linhas = []
    for id_conta, total in somas.items():
        conta = contas_por_id.get(id_conta)
        rotulo = f"{conta['codigo']} - {conta['nome']}" if conta else "Categoria removida"
        linhas.append({"categoria": rotulo, "total": total})
    linhas.sort(key=lambda r: r["total"], reverse=True)
    return linhas
