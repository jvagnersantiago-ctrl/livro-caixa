"""
Camada de acesso a dados do Livro Caixa, conectando direto no PostgreSQL
do Supabase via psycopg2 puro (sem SQLAlchemy / st.connection).

Modelo de dados: cada EMPRESA (CPF/CNPJ) tem seu próprio Plano de
Contas, seus próprios Clientes e seus próprios Lançamentos — são times
completamente separados. Usuários (pessoas que fazem login) recebem
acesso a uma ou mais empresas via a tabela usuario_empresa; o
administrador tem acesso a todas, sempre, sem precisar de linha nessa
tabela de permissão.

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
import psycopg2.extras
import streamlit as st


@st.cache_resource
def _conectar():
    # =====================================================================
    # DIAGNÓSTICO TEMPORÁRIO — REMOVER DEPOIS DE RESOLVER O ERRO DE CONEXÃO.
    # Mostra a mensagem real do psycopg2/Postgres na tela (o Streamlit Cloud
    # esconde detalhes de exceção não tratada por padrão). Depois que a
    # conexão estiver funcionando, tire este try/except e volte a deixar
    # a função levantar a exceção normalmente.
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


def _executar_varios(conn, comandos: list[tuple[str, dict]]) -> None:
    """
    Executa vários INSERT/UPDATE/DELETE numa única transação (um
    commit só no final). Usado quando uma ação lógica precisa de mais
    de um comando SQL pra não ficar pela metade se algo falhar no meio
    — ex: criar uma empresa E semear o plano de contas padrão dela;
    ou substituir todas as permissões de acesso de um usuário.
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        try:
            resultado = None
            for sql, params in comandos:
                cur.execute(sql, params)
                if cur.description:
                    resultado = [dict(r) for r in cur.fetchall()]
            conn.commit()
            return resultado
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
# Usuários (login) — não têm mais relação direta com CPF/CNPJ próprio;
# o CPF/CNPJ que importa agora é o da empresa, não o da pessoa.
# ---------------------------------------------------------------------

def autenticar(conn, nome: str, senha_hash: str):
    """Devolve o usuário (dict) se nome+hash baterem, senão None."""
    linhas = _consultar(
        conn,
        "SELECT id_usuario, nome, cpf_cnpj, is_admin FROM usuarios "
        "WHERE nome = %(nome)s AND senha_hash = %(senha_hash)s",
        {"nome": nome, "senha_hash": senha_hash},
    )
    return linhas[0] if linhas else None


def listar_usuarios(conn):
    return _consultar(
        conn,
        "SELECT id_usuario, nome, cpf_cnpj, is_admin FROM usuarios ORDER BY nome",
    )


def buscar_usuario_por_id(conn, id_usuario: int):
    linhas = _consultar(
        conn,
        "SELECT id_usuario, nome, cpf_cnpj, is_admin FROM usuarios WHERE id_usuario = %(id)s",
        {"id": id_usuario},
    )
    return linhas[0] if linhas else None


def inserir_usuario(conn, nome: str, senha_hash: str, cpf_cnpj, is_admin: bool) -> None:
    """Levanta psycopg2.IntegrityError se já existir usuário com esse nome."""
    _executar_escrita(
        conn,
        "INSERT INTO usuarios (nome, senha_hash, cpf_cnpj, is_admin) "
        "VALUES (%(nome)s, %(senha_hash)s, %(cpf_cnpj)s, %(is_admin)s)",
        {"nome": nome, "senha_hash": senha_hash, "cpf_cnpj": cpf_cnpj, "is_admin": is_admin},
    )


def atualizar_usuario(conn, id_usuario, nome, senha_hash, cpf_cnpj, is_admin) -> None:
    """senha_hash=None mantém a senha atual."""
    if senha_hash is None:
        _executar_escrita(
            conn,
            "UPDATE usuarios SET nome = %(nome)s, cpf_cnpj = %(cpf_cnpj)s, is_admin = %(is_admin)s "
            "WHERE id_usuario = %(id)s",
            {"id": id_usuario, "nome": nome, "cpf_cnpj": cpf_cnpj, "is_admin": is_admin},
        )
    else:
        _executar_escrita(
            conn,
            "UPDATE usuarios SET nome = %(nome)s, senha_hash = %(senha_hash)s, "
            "cpf_cnpj = %(cpf_cnpj)s, is_admin = %(is_admin)s WHERE id_usuario = %(id)s",
            {"id": id_usuario, "nome": nome, "senha_hash": senha_hash,
             "cpf_cnpj": cpf_cnpj, "is_admin": is_admin},
        )


def excluir_usuario(conn, id_usuario: int) -> None:
    """As permissões de acesso (usuario_empresa) somem em cascata —
    sem risco, é só tabela de permissão. Não afeta lançamentos."""
    _executar_escrita(conn, "DELETE FROM usuarios WHERE id_usuario = %(id)s", {"id": id_usuario})


# ---------------------------------------------------------------------
# Empresas + controle de acesso (usuario_empresa)
# ---------------------------------------------------------------------

# Mesmo plano de contas padrão usado desde a versão SQLite original.
_PLANO_CONTAS_PADRAO = [
    ("1",   "Receitas",                     None, "Receita"),
    ("1.1", "Receitas de Serviços",         "1",  "Receita"),
    ("1.2", "Receitas de Vendas",           "1",  "Receita"),
    ("1.3", "Outras Receitas",              "1",  "Receita"),
    ("2",   "Despesas Fixas",               None, "Despesa"),
    ("2.1", "Aluguel",                      "2",  "Despesa"),
    ("2.2", "Salários e Pró-labore",        "2",  "Despesa"),
    ("2.3", "Contabilidade",                "2",  "Despesa"),
    ("2.4", "Assinaturas e Softwares",      "2",  "Despesa"),
    ("3",   "Despesas Variáveis",           None, "Despesa"),
    ("3.1", "Marketing e Publicidade",      "3",  "Despesa"),
    ("3.2", "Materiais e Insumos",          "3",  "Despesa"),
    ("3.3", "Comissões",                    "3",  "Despesa"),
    ("3.4", "Manutenção",                   "3",  "Despesa"),
    ("4",   "Impostos",                     None, "Despesa"),
    ("4.1", "DAS (Simples Nacional / MEI)", "4",  "Despesa"),
    ("4.2", "ISS",                          "4",  "Despesa"),
    ("4.3", "IRPJ/CSLL",                    "4",  "Despesa"),
]


def listar_empresas(conn):
    return _consultar(conn, "SELECT id_empresa, cpf_cnpj, nome FROM empresas ORDER BY nome")


def buscar_empresa_por_id(conn, id_empresa: int):
    linhas = _consultar(
        conn, "SELECT id_empresa, cpf_cnpj, nome FROM empresas WHERE id_empresa = %(id)s",
        {"id": id_empresa},
    )
    return linhas[0] if linhas else None


def inserir_empresa(conn, cpf_cnpj: str, nome: str) -> None:
    """
    Cria a empresa E já semeia o plano de contas padrão dela numa única
    transação — uma empresa sem nenhuma conta cadastrada é um beco sem
    saída (não dá pra lançar nada), então isso evita esse estado inútil.
    Levanta psycopg2.IntegrityError se já existir empresa com esse CPF/CNPJ.
    """
    resultado = _executar_varios(conn, [(
        "INSERT INTO empresas (cpf_cnpj, nome) VALUES (%(cpf_cnpj)s, %(nome)s) "
        "RETURNING id_empresa",
        {"cpf_cnpj": cpf_cnpj, "nome": nome},
    )])
    id_empresa = resultado[0]["id_empresa"]

    codigo_para_id: dict[str, int] = {}
    comandos_contas = []
    # Como cada INSERT de conta depende do id_pai já ter sido inserido,
    # semeamos em duas passadas: raízes primeiro, depois filhas — mas
    # como não sabemos o id gerado sem executar, fazemos uma chamada
    # por nível em vez de tentar prever os ids.
    for codigo, nome_conta, codigo_pai, natureza in _PLANO_CONTAS_PADRAO:
        id_pai = codigo_para_id.get(codigo_pai) if codigo_pai else None
        linha = _executar_varios(conn, [(
            "INSERT INTO plano_contas (codigo, nome, id_pai, natureza, id_empresa) "
            "VALUES (%(codigo)s, %(nome)s, %(id_pai)s, %(natureza)s, %(id_empresa)s) "
            "RETURNING id_conta",
            {"codigo": codigo, "nome": nome_conta, "id_pai": id_pai,
             "natureza": natureza, "id_empresa": id_empresa},
        )])
        codigo_para_id[codigo] = linha[0]["id_conta"]


def atualizar_empresa(conn, id_empresa, cpf_cnpj, nome) -> None:
    _executar_escrita(
        conn,
        "UPDATE empresas SET cpf_cnpj = %(cpf_cnpj)s, nome = %(nome)s WHERE id_empresa = %(id)s",
        {"id": id_empresa, "cpf_cnpj": cpf_cnpj, "nome": nome},
    )


def excluir_empresa(conn, id_empresa: int) -> None:
    """Levanta psycopg2.IntegrityError se a empresa tiver lançamentos,
    clientes ou plano de contas vinculados — sem cascata pra dado real."""
    _executar_escrita(conn, "DELETE FROM empresas WHERE id_empresa = %(id)s", {"id": id_empresa})


def listar_empresas_do_usuario(conn, id_usuario: int):
    """Empresas que este usuário específico pode acessar (ignora
    is_admin — quem decide 'admin vê tudo' é o app.py, não aqui)."""
    return _consultar(
        conn,
        """
        SELECT e.id_empresa, e.cpf_cnpj, e.nome
        FROM empresas e
        JOIN usuario_empresa ue ON ue.id_empresa = e.id_empresa
        WHERE ue.id_usuario = %(id)s
        ORDER BY e.nome
        """,
        {"id": id_usuario},
    )


def definir_acessos_usuario(conn, id_usuario: int, ids_empresas: list[int]) -> None:
    """Substitui todas as permissões de acesso do usuário pelo conjunto
    informado (apaga as antigas, insere as novas, tudo numa transação)."""
    comandos = [("DELETE FROM usuario_empresa WHERE id_usuario = %(id)s", {"id": id_usuario})]
    for id_empresa in ids_empresas:
        comandos.append((
            "INSERT INTO usuario_empresa (id_usuario, id_empresa) VALUES (%(id_usuario)s, %(id_empresa)s)",
            {"id_usuario": id_usuario, "id_empresa": id_empresa},
        ))
    _executar_varios(conn, comandos)


# ---------------------------------------------------------------------
# Clientes (agora por empresa)
# ---------------------------------------------------------------------

def listar_clientes(conn, id_empresa: int):
    return _consultar(
        conn,
        "SELECT id_cliente, nome, cpf, telefone, email, observacoes "
        "FROM clientes WHERE id_empresa = %(id_empresa)s ORDER BY nome",
        {"id_empresa": id_empresa},
    )


def inserir_cliente(conn, id_empresa, nome, cpf, telefone, email, observacoes) -> None:
    _executar_escrita(
        conn,
        """
        INSERT INTO clientes (id_empresa, nome, cpf, telefone, email, observacoes)
        VALUES (%(id_empresa)s, %(nome)s, %(cpf)s, %(telefone)s, %(email)s, %(observacoes)s)
        """,
        {"id_empresa": id_empresa, "nome": nome, "cpf": cpf, "telefone": telefone,
         "email": email, "observacoes": observacoes},
    )


def buscar_cliente_por_id(conn, id_cliente: int):
    linhas = _consultar(
        conn,
        "SELECT id_cliente, id_empresa, nome, cpf, telefone, email, observacoes "
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
    """Levanta psycopg2.IntegrityError se houver lançamentos vinculados
    a este cliente."""
    _executar_escrita(conn, "DELETE FROM clientes WHERE id_cliente = %(id)s", {"id": id_cliente})


# ---------------------------------------------------------------------
# Plano de Contas (agora por empresa)
# ---------------------------------------------------------------------

def listar_todas_contas(conn, id_empresa: int):
    return _consultar(
        conn,
        "SELECT id_conta, codigo, nome, id_pai, natureza "
        "FROM plano_contas WHERE id_empresa = %(id_empresa)s ORDER BY codigo",
        {"id_empresa": id_empresa},
    )


def listar_plano_contas_folhas(conn, id_empresa: int):
    """Contas sem filhos — só elas podem receber lançamento."""
    return _consultar(
        conn,
        """
        SELECT id_conta, codigo, nome
        FROM plano_contas
        WHERE id_empresa = %(id_empresa)s
          AND id_conta NOT IN (
              SELECT DISTINCT id_pai FROM plano_contas
              WHERE id_pai IS NOT NULL AND id_empresa = %(id_empresa)s
          )
        ORDER BY codigo
        """,
        {"id_empresa": id_empresa},
    )


def buscar_conta_por_id(conn, id_conta: int):
    linhas = _consultar(
        conn,
        "SELECT id_conta, id_empresa, codigo, nome, id_pai, natureza "
        "FROM plano_contas WHERE id_conta = %(id)s",
        {"id": id_conta},
    )
    return linhas[0] if linhas else None


def _proximo_codigo(conn, id_empresa, id_pai) -> str:
    if id_pai is None:
        linhas = _consultar(
            conn, "SELECT codigo FROM plano_contas WHERE id_empresa = %(id_empresa)s AND id_pai IS NULL",
            {"id_empresa": id_empresa},
        )
        numeros = [int(r["codigo"]) for r in linhas if r["codigo"].isdigit()]
        return str(max(numeros, default=0) + 1)

    pai = _consultar(conn, "SELECT codigo FROM plano_contas WHERE id_conta = %(id)s", {"id": id_pai})
    codigo_pai = pai[0]["codigo"]

    filhos = _consultar(conn, "SELECT codigo FROM plano_contas WHERE id_pai = %(id_pai)s", {"id_pai": id_pai})
    sufixos = [int(f["codigo"].split(".")[-1]) for f in filhos if f["codigo"].split(".")[-1].isdigit()]
    return f"{codigo_pai}.{max(sufixos, default=0) + 1}"


def inserir_conta(conn, id_empresa: int, nome: str, id_pai, natureza: str) -> None:
    codigo = _proximo_codigo(conn, id_empresa, id_pai)
    _executar_escrita(
        conn,
        "INSERT INTO plano_contas (codigo, nome, id_pai, natureza, id_empresa) "
        "VALUES (%(codigo)s, %(nome)s, %(id_pai)s, %(natureza)s, %(id_empresa)s)",
        {"codigo": codigo, "nome": nome, "id_pai": id_pai, "natureza": natureza, "id_empresa": id_empresa},
    )


def excluir_conta(conn, id_conta: int) -> None:
    """Levanta psycopg2.IntegrityError se a conta tiver subcontas ou
    lançamentos vinculados — sem cascata."""
    _executar_escrita(conn, "DELETE FROM plano_contas WHERE id_conta = %(id)s", {"id": id_conta})


# ---------------------------------------------------------------------
# Lançamentos (agora por empresa, não mais por titular pessoa)
# ---------------------------------------------------------------------

def inserir_lancamento(
    conn, id_empresa, data, tipo, valor, forma_pagamento, descricao, id_cliente, id_conta_plano,
    id_transacao_banco=None,
) -> None:
    _executar_escrita(
        conn,
        """
        INSERT INTO lancamentos
            (id_empresa, data, tipo, valor, forma_pagamento, descricao,
             id_cliente, id_conta_plano, id_transacao_banco)
        VALUES
            (%(id_empresa)s, %(data)s, %(tipo)s, %(valor)s, %(forma_pagamento)s, %(descricao)s,
             %(id_cliente)s, %(id_conta_plano)s, %(id_transacao_banco)s)
        """,
        {"id_empresa": id_empresa, "data": data, "tipo": tipo, "valor": valor,
         "forma_pagamento": forma_pagamento, "descricao": descricao, "id_cliente": id_cliente,
         "id_conta_plano": id_conta_plano, "id_transacao_banco": id_transacao_banco},
    )


def listar_lancamentos(conn, id_empresa=None):
    """id_empresa=None -> todas as empresas (visão geral de admin)."""
    sql = """
        SELECT
            l.id_lancamento AS id,
            l.data,
            l.tipo,
            l.valor,
            l.forma_pagamento,
            l.descricao,
            c.nome AS cliente,
            (pc.codigo || ' - ' || pc.nome) AS categoria,
            emp.nome AS empresa
        FROM lancamentos l
        LEFT JOIN clientes c ON l.id_cliente = c.id_cliente
        JOIN plano_contas pc ON l.id_conta_plano = pc.id_conta
        LEFT JOIN empresas emp ON l.id_empresa = emp.id_empresa
    """
    params = {}
    if id_empresa is not None:
        sql += " WHERE l.id_empresa = %(id_empresa)s"
        params["id_empresa"] = id_empresa
    sql += " ORDER BY l.data DESC, l.id_lancamento DESC"

    linhas = _consultar(conn, sql, params)
    return _normalizar(linhas, colunas_numericas=("valor",), colunas_data=("data",))


def buscar_lancamento_por_id(conn, id_lancamento: int):
    linhas = _consultar(
        conn,
        """
        SELECT id_lancamento, id_empresa, data, tipo, valor, forma_pagamento, descricao,
               id_cliente, id_conta_plano, id_transacao_banco
        FROM lancamentos WHERE id_lancamento = %(id)s
        """,
        {"id": id_lancamento},
    )
    linhas = _normalizar(linhas, colunas_numericas=("valor",), colunas_data=("data",))
    return linhas[0] if linhas else None


def atualizar_lancamento(
    conn, id_lancamento, id_empresa, data, tipo, valor, forma_pagamento, descricao, id_cliente, id_conta_plano,
) -> None:
    _executar_escrita(
        conn,
        """
        UPDATE lancamentos
        SET id_empresa = %(id_empresa)s, data = %(data)s, tipo = %(tipo)s, valor = %(valor)s,
            forma_pagamento = %(forma_pagamento)s, descricao = %(descricao)s,
            id_cliente = %(id_cliente)s, id_conta_plano = %(id_conta_plano)s
        WHERE id_lancamento = %(id)s
        """,
        {"id": id_lancamento, "id_empresa": id_empresa, "data": data, "tipo": tipo, "valor": valor,
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


def buscar_totais_dre(conn, id_empresa=None):
    sql = (
        _CTE_RAIZ
        + """
        SELECT
            to_char(l.data, 'YYYY-MM') AS ano_mes,
            rf.codigo AS raiz_codigo,
            rf.nome AS raiz_nome,
            SUM(l.valor) AS total
        FROM lancamentos l
        JOIN raiz_final rf ON rf.id_conta_original = l.id_conta_plano
        """
    )
    params = {}
    if id_empresa is not None:
        sql += " WHERE l.id_empresa = %(id_empresa)s"
        params["id_empresa"] = id_empresa
    sql += " GROUP BY ano_mes, rf.codigo, rf.nome ORDER BY ano_mes, rf.codigo"

    linhas = _consultar(conn, sql, params)
    return _normalizar(linhas, colunas_numericas=("total",))


def listar_meses_disponiveis(conn, id_empresa=None):
    sql = "SELECT DISTINCT to_char(data, 'YYYY-MM') AS ano_mes FROM lancamentos"
    params = {}
    if id_empresa is not None:
        sql += " WHERE id_empresa = %(id_empresa)s"
        params["id_empresa"] = id_empresa
    sql += " ORDER BY 1"

    linhas = _consultar(conn, sql, params)
    return [l["ano_mes"] for l in linhas]


def buscar_despesas_por_categoria(conn, ano_mes: str, id_empresa=None):
    sql = (
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
        """
    )
    params = {"ano_mes": ano_mes}
    if id_empresa is not None:
        sql += " AND l.id_empresa = %(id_empresa)s"
        params["id_empresa"] = id_empresa
    sql += " GROUP BY pc.id_conta, pc.codigo, pc.nome ORDER BY total DESC"

    linhas = _consultar(conn, sql, params)
    return _normalizar(linhas, colunas_numericas=("total",))
