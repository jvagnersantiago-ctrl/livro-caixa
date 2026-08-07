"""
Interface Streamlit para o Livro Caixa.
Rodar com: streamlit run app.py
Requer que o schema já esteja criado no Supabase (schema_postgres.sql +
migracao_usuarios.sql + migracao_empresas.sql +
migracao_empresas_clientes_plano.sql, rodados uma vez no SQL Editor) e
as credenciais em .streamlit/secrets.toml.
"""

import hashlib
from datetime import date

import pandas as pd
import plotly.express as px
import streamlit as st
import psycopg2

import db

st.set_page_config(page_title="Livro Caixa", page_icon="💰", layout="wide")


# =======================================================================
# AUTENTICAÇÃO
# =======================================================================

def _hash_senha(senha: str) -> str:
    return hashlib.sha256(senha.encode("utf-8")).hexdigest()


def tela_login() -> None:
    st.title("🔒 Livro Caixa")
    st.caption("Entre com seu usuário e senha para continuar.")
    with st.form("form_login"):
        usuario = st.text_input("Usuário")
        senha = st.text_input("Senha", type="password")
        entrar = st.form_submit_button("Entrar")

        if entrar:
            conn_login = db.get_connection()
            perfil = db.autenticar(conn_login, usuario, _hash_senha(senha))
            if perfil:
                st.session_state["autenticado"] = True
                st.session_state["usuario_atual"] = perfil
                st.rerun()
            else:
                st.error("Usuário ou senha incorretos.")


st.session_state.setdefault("autenticado", False)
st.session_state.setdefault("usuario_atual", None)

if not st.session_state["autenticado"]:
    tela_login()
    st.stop()

usuario_atual = st.session_state["usuario_atual"]
conn = db.get_connection()

FORMAS_PAGAMENTO = [
    "Pix", "Dinheiro", "Cartão de Crédito", "Cartão de Débito",
    "Transferência", "Boleto", "Outro",
]


def formatar_moeda(valor: float) -> str:
    texto = f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"


def empresas_do_usuario_logado():
    """Empresas que o usuário logado pode acessar. Admin acessa todas
    sem precisar de linha na tabela de permissão."""
    if usuario_atual["is_admin"]:
        return db.listar_empresas(conn)
    return db.listar_empresas_do_usuario(conn, usuario_atual["id_usuario"])


def selecionar_empresa_trabalho(empresas_acessiveis, key: str):
    """
    Seletor de UMA empresa específica pra trabalhar dentro dela
    (Clientes, Plano de Contas, novo lançamento). Sem opção 'Todas' —
    toda ação de cadastro precisa de uma empresa exata.
    Se só existe 1 empresa acessível, escolhe sozinho sem perguntar.
    """
    if not empresas_acessiveis:
        return None
    if len(empresas_acessiveis) == 1:
        return empresas_acessiveis[0]["id_empresa"]
    opcoes = {f"{e['nome']} ({e['cpf_cnpj']})": e["id_empresa"] for e in empresas_acessiveis}
    escolha = st.selectbox("Empresa", list(opcoes.keys()), key=key)
    return opcoes[escolha]


def selecionar_empresa_filtro(empresas_acessiveis, key: str):
    """
    Seletor pra VISUALIZAR dados (histórico, DRE, Dashboard). Admin
    ganha a opção 'Todas as Empresas' (visão consolidada, só
    informativa — não é um DRE válido pra declarar nada). Quem não é
    admin e só tem 1 empresa não vê seletor nenhum.
    """
    if not empresas_acessiveis:
        return None, None
    if not usuario_atual["is_admin"] and len(empresas_acessiveis) == 1:
        emp = empresas_acessiveis[0]
        return emp["id_empresa"], emp["nome"]

    opcoes = {}
    if usuario_atual["is_admin"]:
        opcoes["Todas as Empresas"] = None
    for e in empresas_acessiveis:
        opcoes[f"{e['nome']} ({e['cpf_cnpj']})"] = e["id_empresa"]
    escolha = st.selectbox("Ver empresa:", list(opcoes.keys()), key=key)
    return opcoes[escolha], escolha


st.sidebar.markdown(f"👤 Logado como **{usuario_atual['nome']}**")
if st.sidebar.button("Sair"):
    st.session_state["autenticado"] = False
    st.session_state["usuario_atual"] = None
    st.rerun()
st.sidebar.markdown("---")

opcoes_menu = ["Clientes", "Lançamentos", "Plano de Contas", "DRE", "Dashboard"]
if usuario_atual["is_admin"]:
    opcoes_menu += ["Empresas", "Usuários"]
pagina = st.sidebar.radio("Navegação", opcoes_menu)

empresas_acessiveis = empresas_do_usuario_logado()

# =======================================================================
# ABA EMPRESAS (só admin)
# =======================================================================
if pagina == "Empresas":
    st.header("Empresas")

    with st.expander("➕ Cadastrar nova empresa"):
        with st.form("form_empresa", clear_on_submit=True):
            nome_emp = st.text_input("Nome / Razão Social *")
            cpf_cnpj_emp = st.text_input("CPF ou CNPJ *")
            enviado_emp = st.form_submit_button("Cadastrar Empresa")

            if enviado_emp:
                if not nome_emp.strip() or not cpf_cnpj_emp.strip():
                    st.error("Nome e CPF/CNPJ são obrigatórios.")
                else:
                    try:
                        db.inserir_empresa(conn, cpf_cnpj_emp.strip(), nome_emp.strip())
                        st.success(
                            f"Empresa '{nome_emp}' criada, já com o plano de contas padrão."
                        )
                        st.rerun()
                    except psycopg2.IntegrityError:
                        st.error("Já existe uma empresa cadastrada com esse CPF/CNPJ.")

    st.subheader("Empresas cadastradas")
    todas_empresas = db.listar_empresas(conn)

    if not todas_empresas:
        st.info("Nenhuma empresa cadastrada ainda.")
    else:
        st.session_state.setdefault("empresa_editando", None)
        st.session_state.setdefault("empresa_excluindo", None)

        cabecalho = st.columns([3, 3, 1, 1])
        for col, titulo in zip(cabecalho, ["Nome / Razão Social", "CPF/CNPJ", "", ""]):
            col.markdown(f"**{titulo}**")

        for e in todas_empresas:
            linha = st.columns([3, 3, 1, 1])
            linha[0].write(e["nome"])
            linha[1].write(e["cpf_cnpj"])
            if linha[2].button("✏️", key=f"editar_empresa_{e['id_empresa']}"):
                st.session_state["empresa_editando"] = e["id_empresa"]
                st.session_state["empresa_excluindo"] = None
                st.rerun()
            if linha[3].button("🗑️", key=f"excluir_empresa_{e['id_empresa']}"):
                st.session_state["empresa_excluindo"] = e["id_empresa"]
                st.session_state["empresa_editando"] = None
                st.rerun()

        if st.session_state["empresa_editando"] is not None:
            registro = db.buscar_empresa_por_id(conn, st.session_state["empresa_editando"])
            st.markdown("---")
            st.subheader(f"Editando: {registro['nome']}")
            with st.form("form_editar_empresa"):
                nome_e = st.text_input("Nome / Razão Social *", value=registro["nome"])
                cpf_cnpj_e = st.text_input("CPF ou CNPJ *", value=registro["cpf_cnpj"])
                col_salvar, col_cancelar = st.columns(2)
                salvar_e = col_salvar.form_submit_button("Salvar Alterações")
                cancelar_e = col_cancelar.form_submit_button("Cancelar")

                if salvar_e:
                    if not nome_e.strip() or not cpf_cnpj_e.strip():
                        st.error("Nome e CPF/CNPJ são obrigatórios.")
                    else:
                        try:
                            db.atualizar_empresa(conn, registro["id_empresa"], cpf_cnpj_e.strip(), nome_e.strip())
                            st.session_state["empresa_editando"] = None
                            st.success("Empresa atualizada.")
                            st.rerun()
                        except psycopg2.IntegrityError:
                            st.error("Já existe outra empresa com esse CPF/CNPJ.")
                if cancelar_e:
                    st.session_state["empresa_editando"] = None
                    st.rerun()

        if st.session_state["empresa_excluindo"] is not None:
            registro = db.buscar_empresa_por_id(conn, st.session_state["empresa_excluindo"])
            st.markdown("---")
            st.warning(f"Confirma a exclusão da empresa **{registro['nome']}**?")
            col_confirmar, col_cancelar = st.columns(2)
            if col_confirmar.button("Confirmar exclusão", type="primary"):
                try:
                    db.excluir_empresa(conn, registro["id_empresa"])
                    st.session_state["empresa_excluindo"] = None
                    st.success("Empresa excluída.")
                    st.rerun()
                except psycopg2.IntegrityError:
                    st.error(
                        "Não é possível excluir: esta empresa tem lançamentos, "
                        "clientes ou plano de contas vinculados."
                    )
            if col_cancelar.button("Cancelar"):
                st.session_state["empresa_excluindo"] = None
                st.rerun()

# =======================================================================
# ABA USUÁRIOS (só admin)
# =======================================================================
elif pagina == "Usuários":
    st.header("Usuários")

    todas_empresas_admin = db.listar_empresas(conn)

    with st.expander("➕ Cadastrar novo usuário"):
        with st.form("form_usuario", clear_on_submit=True):
            col1, col2 = st.columns(2)
            nome_u = col1.text_input("Nome *")
            senha_u = col2.text_input("Senha *", type="password")
            admin_u = st.checkbox("É administrador (acessa todas as empresas automaticamente)")
            empresas_selecionadas = []
            if not admin_u and todas_empresas_admin:
                opcoes_empresa_u = {f"{e['nome']} ({e['cpf_cnpj']})": e["id_empresa"] for e in todas_empresas_admin}
                rotulos_sel = st.multiselect("Dar acesso às empresas:", list(opcoes_empresa_u.keys()))
                empresas_selecionadas = [opcoes_empresa_u[r] for r in rotulos_sel]
            enviado_u = st.form_submit_button("Cadastrar Usuário")

            if enviado_u:
                if not nome_u.strip() or not senha_u:
                    st.error("Nome e senha são obrigatórios.")
                else:
                    try:
                        db.inserir_usuario(conn, nome_u.strip(), _hash_senha(senha_u), None, admin_u)
                        novo = next(u for u in db.listar_usuarios(conn) if u["nome"] == nome_u.strip())
                        if not admin_u and empresas_selecionadas:
                            db.definir_acessos_usuario(conn, novo["id_usuario"], empresas_selecionadas)
                        st.success(f"Usuário '{nome_u}' cadastrado.")
                        st.rerun()
                    except psycopg2.IntegrityError:
                        st.error("Já existe um usuário com esse nome.")

    st.subheader("Usuários cadastrados")
    usuarios = db.listar_usuarios(conn)

    st.session_state.setdefault("usuario_editando", None)
    st.session_state.setdefault("usuario_excluindo", None)

    cabecalho = st.columns([3, 1.5, 1, 1])
    for col, titulo in zip(cabecalho, ["Nome", "Admin?", "", ""]):
        col.markdown(f"**{titulo}**")

    for u in usuarios:
        linha = st.columns([3, 1.5, 1, 1])
        linha[0].write(u["nome"])
        linha[1].write("✅ (todas as empresas)" if u["is_admin"] else "—")
        if linha[2].button("✏️", key=f"editar_usuario_{u['id_usuario']}"):
            st.session_state["usuario_editando"] = u["id_usuario"]
            st.session_state["usuario_excluindo"] = None
            st.rerun()
        if linha[3].button("🗑️", key=f"excluir_usuario_{u['id_usuario']}"):
            st.session_state["usuario_excluindo"] = u["id_usuario"]
            st.session_state["usuario_editando"] = None
            st.rerun()

    if st.session_state["usuario_editando"] is not None:
        registro = db.buscar_usuario_por_id(conn, st.session_state["usuario_editando"])
        acessos_atuais = {e["id_empresa"] for e in db.listar_empresas_do_usuario(conn, registro["id_usuario"])}
        st.markdown("---")
        st.subheader(f"Editando: {registro['nome']}")
        with st.form("form_editar_usuario"):
            col1, col2 = st.columns(2)
            nome_e = col1.text_input("Nome *", value=registro["nome"])
            senha_e = col2.text_input("Nova senha (deixe em branco pra manter a atual)", type="password")
            admin_e = st.checkbox(
                "É administrador (acessa todas as empresas automaticamente)", value=registro["is_admin"]
            )
            empresas_sel_edit = []
            if not admin_e and todas_empresas_admin:
                opcoes_empresa_e = {f"{e['nome']} ({e['cpf_cnpj']})": e["id_empresa"] for e in todas_empresas_admin}
                id_para_rotulo_emp = {v: k for k, v in opcoes_empresa_e.items()}
                padrao_selecionado = [id_para_rotulo_emp[i] for i in acessos_atuais if i in id_para_rotulo_emp]
                rotulos_sel_edit = st.multiselect(
                    "Empresas com acesso:", list(opcoes_empresa_e.keys()), default=padrao_selecionado
                )
                empresas_sel_edit = [opcoes_empresa_e[r] for r in rotulos_sel_edit]

            col_salvar, col_cancelar = st.columns(2)
            salvar_u = col_salvar.form_submit_button("Salvar Alterações")
            cancelar_u = col_cancelar.form_submit_button("Cancelar")

            if salvar_u:
                admins_restantes = sum(1 for u in usuarios if u["is_admin"] and u["id_usuario"] != registro["id_usuario"])
                if registro["is_admin"] and not admin_e and admins_restantes == 0:
                    st.error("Não é possível remover o último administrador do sistema.")
                elif not nome_e.strip():
                    st.error("Informe o nome do usuário.")
                else:
                    try:
                        db.atualizar_usuario(
                            conn, registro["id_usuario"], nome_e.strip(),
                            _hash_senha(senha_e) if senha_e else None, None, admin_e,
                        )
                        if not admin_e:
                            db.definir_acessos_usuario(conn, registro["id_usuario"], empresas_sel_edit)
                        st.session_state["usuario_editando"] = None
                        st.success("Usuário atualizado.")
                        st.rerun()
                    except psycopg2.IntegrityError:
                        st.error("Já existe outro usuário com esse nome.")
            if cancelar_u:
                st.session_state["usuario_editando"] = None
                st.rerun()

    if st.session_state["usuario_excluindo"] is not None:
        registro = db.buscar_usuario_por_id(conn, st.session_state["usuario_excluindo"])
        st.markdown("---")
        if registro["id_usuario"] == usuario_atual["id_usuario"]:
            st.error("Você não pode excluir o usuário com o qual está logado agora.")
            st.session_state["usuario_excluindo"] = None
        else:
            admins_restantes = sum(1 for u in usuarios if u["is_admin"] and u["id_usuario"] != registro["id_usuario"])
            if registro["is_admin"] and admins_restantes == 0:
                st.error("Não é possível excluir o último administrador do sistema.")
                st.session_state["usuario_excluindo"] = None
            else:
                st.warning(f"Confirma a exclusão do usuário **{registro['nome']}**?")
                col_confirmar, col_cancelar = st.columns(2)
                if col_confirmar.button("Confirmar exclusão", type="primary"):
                    db.excluir_usuario(conn, registro["id_usuario"])
                    st.session_state["usuario_excluindo"] = None
                    st.success("Usuário excluído.")
                    st.rerun()
                if col_cancelar.button("Cancelar"):
                    st.session_state["usuario_excluindo"] = None
                    st.rerun()

# =======================================================================
# ABA CLIENTES
# =======================================================================
elif pagina == "Clientes":
    st.header("Clientes")

    if not empresas_acessiveis:
        st.warning(
            "Você ainda não tem acesso a nenhuma empresa. Peça ao administrador "
            "pra conceder acesso, ou cadastre uma empresa na aba 'Empresas'."
        )
    else:
        id_empresa_trabalho = selecionar_empresa_trabalho(empresas_acessiveis, key="empresa_clientes")

        with st.expander("➕ Cadastrar novo cliente"):
            with st.form("form_cliente", clear_on_submit=True):
                col1, col2 = st.columns(2)
                nome = col1.text_input("Nome *")
                cpf = col2.text_input("CPF")
                telefone = col1.text_input("Telefone")
                email = col2.text_input("E-mail")
                observacoes = st.text_area("Observações")
                enviado = st.form_submit_button("Cadastrar Cliente")

                if enviado:
                    if not nome.strip():
                        st.error("Informe o nome do cliente.")
                    else:
                        try:
                            db.inserir_cliente(
                                conn, id_empresa_trabalho, nome.strip(),
                                cpf.strip() or None, telefone.strip() or None,
                                email.strip() or None, observacoes.strip() or None,
                            )
                            st.success(f"Cliente '{nome}' cadastrado.")
                            st.rerun()
                        except psycopg2.IntegrityError:
                            st.error("Já existe um cliente com esse CPF nesta empresa.")

        st.subheader("Clientes cadastrados")
        clientes = db.listar_clientes(conn, id_empresa_trabalho)

        if not clientes:
            st.info("Nenhum cliente cadastrado ainda nesta empresa.")
        else:
            st.session_state.setdefault("cliente_editando", None)
            st.session_state.setdefault("cliente_excluindo", None)

            cabecalho = st.columns([3, 2, 2, 3, 1, 1])
            for col, titulo in zip(cabecalho, ["Nome", "CPF", "Telefone", "E-mail", "", ""]):
                col.markdown(f"**{titulo}**")

            for c in clientes:
                linha = st.columns([3, 2, 2, 3, 1, 1])
                linha[0].write(c["nome"] or "—")
                linha[1].write(c["cpf"] or "—")
                linha[2].write(c["telefone"] or "—")
                linha[3].write(c["email"] or "—")
                if linha[4].button("✏️", key=f"editar_cliente_{c['id_cliente']}"):
                    st.session_state["cliente_editando"] = c["id_cliente"]
                    st.session_state["cliente_excluindo"] = None
                    st.rerun()
                if linha[5].button("🗑️", key=f"excluir_cliente_{c['id_cliente']}"):
                    st.session_state["cliente_excluindo"] = c["id_cliente"]
                    st.session_state["cliente_editando"] = None
                    st.rerun()

            if st.session_state["cliente_editando"] is not None:
                registro = db.buscar_cliente_por_id(conn, st.session_state["cliente_editando"])
                st.markdown("---")
                st.subheader(f"Editando: {registro['nome']}")
                with st.form("form_editar_cliente"):
                    col1, col2 = st.columns(2)
                    nome_e = col1.text_input("Nome *", value=registro["nome"] or "")
                    cpf_e = col2.text_input("CPF", value=registro["cpf"] or "")
                    telefone_e = col1.text_input("Telefone", value=registro["telefone"] or "")
                    email_e = col2.text_input("E-mail", value=registro["email"] or "")
                    observacoes_e = st.text_area("Observações", value=registro["observacoes"] or "")

                    col_salvar, col_cancelar = st.columns(2)
                    salvar = col_salvar.form_submit_button("Salvar Alterações")
                    cancelar = col_cancelar.form_submit_button("Cancelar")

                    if salvar:
                        if not nome_e.strip():
                            st.error("Informe o nome do cliente.")
                        else:
                            try:
                                db.atualizar_cliente(
                                    conn, registro["id_cliente"], nome_e.strip(),
                                    cpf_e.strip() or None, telefone_e.strip() or None,
                                    email_e.strip() or None, observacoes_e.strip() or None,
                                )
                                st.session_state["cliente_editando"] = None
                                st.success("Cliente atualizado.")
                                st.rerun()
                            except psycopg2.IntegrityError:
                                st.error("Já existe outro cliente com esse CPF nesta empresa.")
                    if cancelar:
                        st.session_state["cliente_editando"] = None
                        st.rerun()

            if st.session_state["cliente_excluindo"] is not None:
                registro = db.buscar_cliente_por_id(conn, st.session_state["cliente_excluindo"])
                st.markdown("---")
                st.warning(f"Confirma a exclusão do cliente **{registro['nome']}**?")
                col_confirmar, col_cancelar = st.columns(2)
                if col_confirmar.button("Confirmar exclusão", type="primary"):
                    try:
                        db.excluir_cliente(conn, registro["id_cliente"])
                        st.session_state["cliente_excluindo"] = None
                        st.success("Cliente excluído.")
                        st.rerun()
                    except psycopg2.IntegrityError:
                        st.error(
                            "Não é possível excluir: este cliente tem lançamentos vinculados a ele."
                        )
                if col_cancelar.button("Cancelar"):
                    st.session_state["cliente_excluindo"] = None
                    st.rerun()

# =======================================================================
# ABA LANÇAMENTOS
# =======================================================================
elif pagina == "Lançamentos":
    st.header("Lançamentos (Fluxo de Caixa)")

    if not empresas_acessiveis:
        st.warning(
            "Você ainda não tem acesso a nenhuma empresa. Peça ao administrador "
            "pra conceder acesso, ou cadastre uma empresa na aba 'Empresas'."
        )
    else:
        id_empresa_novo = selecionar_empresa_trabalho(empresas_acessiveis, key="empresa_novo_lancamento")

        clientes = db.listar_clientes(conn, id_empresa_novo)
        plano_contas = db.listar_plano_contas_folhas(conn, id_empresa_novo)

        if not plano_contas:
            st.warning("Esta empresa não tem contas cadastradas no Plano de Contas ainda.")
        else:
            opcoes_cliente = {"Nenhum": None}
            opcoes_cliente.update(
                {f"{c['nome']} ({c['cpf'] or 'sem CPF'})": c["id_cliente"] for c in clientes}
            )
            opcoes_categoria = {
                f"{c['codigo']} - {c['nome']}": c["id_conta"] for c in plano_contas
            }

            with st.expander("➕ Registrar novo lançamento"):
                with st.form("form_lancamento", clear_on_submit=True):
                    col1, col2 = st.columns(2)
                    data_lanc = col1.date_input("Data", value=date.today())
                    tipo = col2.radio("Tipo", ["Entrada", "Saída"], horizontal=True)
                    valor = col1.number_input(
                        "Valor (R$)", min_value=0.01, step=0.01, format="%.2f"
                    )
                    forma_pagamento = col2.selectbox("Forma de Pagamento", FORMAS_PAGAMENTO)
                    cliente_sel = col1.selectbox("Cliente", list(opcoes_cliente.keys()))
                    categoria_sel = col2.selectbox("Categoria", list(opcoes_categoria.keys()))
                    descricao = st.text_input("Descrição")

                    enviado = st.form_submit_button("Registrar Lançamento")

                    if enviado:
                        db.inserir_lancamento(
                            conn, id_empresa_novo,
                            data=data_lanc.isoformat(), tipo=tipo, valor=valor,
                            forma_pagamento=forma_pagamento, descricao=descricao.strip() or None,
                            id_cliente=opcoes_cliente[cliente_sel],
                            id_conta_plano=opcoes_categoria[categoria_sel],
                        )
                        st.success("Lançamento registrado.")
                        st.rerun()

    st.subheader("Histórico de Lançamentos")
    id_empresa_filtro, _ = selecionar_empresa_filtro(empresas_acessiveis, key="empresa_historico")
    lancamentos = db.listar_lancamentos(conn, id_empresa=id_empresa_filtro)

    if not lancamentos:
        st.info("Nenhum lançamento registrado ainda.")
    else:
        st.session_state.setdefault("lancamento_editando", None)
        st.session_state.setdefault("lancamento_excluindo", None)

        mostrar_empresa = id_empresa_filtro is None
        larguras = [1.4, 0.9, 1.2, 1.4, 2.2, 1.7] + ([1.5] if mostrar_empresa else []) + [0.8, 0.8]
        titulos = ["Data", "Tipo", "Valor", "Forma Pgto.", "Categoria", "Cliente"]
        if mostrar_empresa:
            titulos.append("Empresa")
        titulos += ["", ""]

        cabecalho = st.columns(larguras)
        for col, titulo in zip(cabecalho, titulos):
            col.markdown(f"**{titulo}**")

        for l in lancamentos:
            linha = st.columns(larguras)
            linha[0].write(l["data"])
            linha[1].write(l["tipo"])
            linha[2].write(formatar_moeda(l["valor"]))
            linha[3].write(l["forma_pagamento"] or "—")
            linha[4].write(l["categoria"])
            linha[5].write(l["cliente"] or "—")
            idx = 6
            if mostrar_empresa:
                linha[idx].write(l["empresa"] or "—")
                idx += 1
            if linha[idx].button("✏️", key=f"editar_lanc_{l['id']}"):
                st.session_state["lancamento_editando"] = l["id"]
                st.session_state["lancamento_excluindo"] = None
                st.rerun()
            if linha[idx + 1].button("🗑️", key=f"excluir_lanc_{l['id']}"):
                st.session_state["lancamento_excluindo"] = l["id"]
                st.session_state["lancamento_editando"] = None
                st.rerun()
            if l["descricao"]:
                st.caption(f"📝 {l['descricao']}")

        # --- Formulário de edição ---
        if st.session_state["lancamento_editando"] is not None:
            registro = db.buscar_lancamento_por_id(conn, st.session_state["lancamento_editando"])

            clientes_edit = db.listar_clientes(conn, registro["id_empresa"])
            plano_contas_edit = db.listar_plano_contas_folhas(conn, registro["id_empresa"])
            opcoes_cliente_edit = {"Nenhum": None}
            opcoes_cliente_edit.update(
                {f"{c['nome']} ({c['cpf'] or 'sem CPF'})": c["id_cliente"] for c in clientes_edit}
            )
            opcoes_categoria_edit = {
                f"{c['codigo']} - {c['nome']}": c["id_conta"] for c in plano_contas_edit
            }

            id_para_label_cliente = {v: k for k, v in opcoes_cliente_edit.items()}
            id_para_label_categoria = {v: k for k, v in opcoes_categoria_edit.items()}
            label_cliente_atual = id_para_label_cliente.get(registro["id_cliente"], "Nenhum")
            label_categoria_atual = id_para_label_categoria.get(registro["id_conta_plano"])

            st.markdown("---")
            st.subheader(f"Editando lançamento #{registro['id_lancamento']}")

            if label_categoria_atual is None:
                st.warning(
                    "A categoria original deste lançamento não está mais disponível "
                    "como conta-folha. Escolha uma nova categoria ao salvar."
                )
                indice_categoria = 0
            else:
                indice_categoria = list(opcoes_categoria_edit.keys()).index(label_categoria_atual)

            with st.form("form_editar_lancamento"):
                col1, col2 = st.columns(2)
                data_edit = col1.date_input("Data", value=date.fromisoformat(registro["data"]))
                tipo_edit = col2.radio(
                    "Tipo", ["Entrada", "Saída"], horizontal=True,
                    index=["Entrada", "Saída"].index(registro["tipo"]),
                )
                valor_edit = col1.number_input(
                    "Valor (R$)", min_value=0.01, step=0.01, format="%.2f",
                    value=float(registro["valor"]),
                )
                forma_pagamento_edit = col2.selectbox(
                    "Forma de Pagamento", FORMAS_PAGAMENTO,
                    index=FORMAS_PAGAMENTO.index(registro["forma_pagamento"])
                    if registro["forma_pagamento"] in FORMAS_PAGAMENTO else 0,
                )
                cliente_sel_edit = col1.selectbox(
                    "Cliente", list(opcoes_cliente_edit.keys()),
                    index=list(opcoes_cliente_edit.keys()).index(label_cliente_atual),
                )
                categoria_sel_edit = col2.selectbox(
                    "Categoria", list(opcoes_categoria_edit.keys()), index=indice_categoria
                )
                descricao_edit = st.text_input("Descrição", value=registro["descricao"] or "")

                st.caption(
                    "A empresa deste lançamento não muda por aqui — edite dentro "
                    "da empresa correta, selecionada acima."
                )

                col_salvar, col_cancelar = st.columns(2)
                salvar = col_salvar.form_submit_button("Salvar Alterações")
                cancelar = col_cancelar.form_submit_button("Cancelar")

                if salvar:
                    db.atualizar_lancamento(
                        conn,
                        id_lancamento=registro["id_lancamento"],
                        id_empresa=registro["id_empresa"],
                        data=data_edit.isoformat(), tipo=tipo_edit, valor=valor_edit,
                        forma_pagamento=forma_pagamento_edit, descricao=descricao_edit.strip() or None,
                        id_cliente=opcoes_cliente_edit[cliente_sel_edit],
                        id_conta_plano=opcoes_categoria_edit[categoria_sel_edit],
                    )
                    st.session_state["lancamento_editando"] = None
                    st.success("Lançamento atualizado.")
                    st.rerun()
                if cancelar:
                    st.session_state["lancamento_editando"] = None
                    st.rerun()

        # --- Confirmação de exclusão ---
        if st.session_state["lancamento_excluindo"] is not None:
            registro = db.buscar_lancamento_por_id(conn, st.session_state["lancamento_excluindo"])
            st.markdown("---")
            st.warning(
                f"Confirma a exclusão do lançamento #{registro['id_lancamento']} "
                f"de {formatar_moeda(registro['valor'])} em {registro['data']}? "
                "Esta ação é irreversível."
            )
            col_confirmar, col_cancelar = st.columns(2)
            if col_confirmar.button("Confirmar exclusão", type="primary"):
                db.excluir_lancamento(conn, registro["id_lancamento"])
                st.session_state["lancamento_excluindo"] = None
                st.success("Lançamento excluído.")
                st.rerun()
            if col_cancelar.button("Cancelar"):
                st.session_state["lancamento_excluindo"] = None
                st.rerun()

# =======================================================================
# ABA PLANO DE CONTAS
# =======================================================================
elif pagina == "Plano de Contas":
    st.header("Gerenciador do Plano de Contas")

    if not empresas_acessiveis:
        st.warning(
            "Você ainda não tem acesso a nenhuma empresa. Peça ao administrador "
            "pra conceder acesso, ou cadastre uma empresa na aba 'Empresas'."
        )
    else:
        id_empresa_pc = selecionar_empresa_trabalho(empresas_acessiveis, key="empresa_plano_contas")
        todas_contas = db.listar_todas_contas(conn, id_empresa_pc)

        st.subheader("Árvore atual")
        if not todas_contas:
            st.info("Nenhuma conta cadastrada ainda nesta empresa.")
        else:
            filhos_de = {}
            contas_por_id = {}
            for c in todas_contas:
                contas_por_id[c["id_conta"]] = c
                filhos_de.setdefault(c["id_pai"], []).append(c)
            for lista in filhos_de.values():
                lista.sort(key=lambda c: c["codigo"])

            def imprimir_arvore(id_pai, profundidade=0):
                for conta in filhos_de.get(id_pai, []):
                    emoji = "📁" if conta["id_conta"] in filhos_de else "📄"
                    st.markdown(
                        "&nbsp;" * (profundidade * 4)
                        + f"{emoji} `{conta['codigo']}` {conta['nome']} "
                        f"_({conta['natureza']})_",
                        unsafe_allow_html=True,
                    )
                    imprimir_arvore(conta["id_conta"], profundidade + 1)

            imprimir_arvore(None)

        st.markdown("---")
        col_criar, col_excluir = st.columns(2)

        with col_criar:
            st.subheader("➕ Nova conta")
            opcoes_pai = {"Nenhuma (conta raiz)": None}
            opcoes_pai.update(
                {f"{c['codigo']} - {c['nome']}": c["id_conta"] for c in todas_contas}
            )
            with st.form("form_nova_conta", clear_on_submit=True):
                nome_conta = st.text_input("Nome da conta *")
                pai_sel = st.selectbox("Conta pai", list(opcoes_pai.keys()))
                id_pai_sel = opcoes_pai[pai_sel]

                if id_pai_sel is None:
                    natureza_sel = st.radio("Natureza", ["Receita", "Despesa"], horizontal=True)
                else:
                    natureza_sel = next(
                        c["natureza"] for c in todas_contas if c["id_conta"] == id_pai_sel
                    )
                    st.caption(f"Natureza herdada da conta pai: **{natureza_sel}**")

                criar = st.form_submit_button("Criar Conta")
                if criar:
                    if not nome_conta.strip():
                        st.error("Informe o nome da conta.")
                    else:
                        db.inserir_conta(conn, id_empresa_pc, nome_conta.strip(), id_pai_sel, natureza_sel)
                        st.success(f"Conta '{nome_conta}' criada.")
                        st.rerun()

        with col_excluir:
            st.subheader("🗑️ Excluir conta")
            if not todas_contas:
                st.info("Nenhuma conta pra excluir.")
            else:
                opcoes_excluir = {
                    f"{c['codigo']} - {c['nome']}": c["id_conta"] for c in todas_contas
                }
                conta_excluir_sel = st.selectbox(
                    "Selecione a conta", list(opcoes_excluir.keys()), key="select_excluir_conta"
                )
                st.caption(
                    "⚠️ Contas com subcontas ou com lançamentos vinculados não "
                    "podem ser excluídas — o banco bloqueia automaticamente."
                )
                if st.button("Excluir Conta Selecionada"):
                    id_conta_excluir = opcoes_excluir[conta_excluir_sel]
                    try:
                        db.excluir_conta(conn, id_conta_excluir)
                        st.success("Conta excluída.")
                        st.rerun()
                    except psycopg2.IntegrityError:
                        st.error(
                            "Não é possível excluir: esta conta tem subcontas ou "
                            "lançamentos vinculados a ela."
                        )

# =======================================================================
# ABA DRE
# =======================================================================
elif pagina == "DRE":
    st.header("DRE — Demonstração do Resultado do Exercício")

    if not empresas_acessiveis:
        st.warning("Você ainda não tem acesso a nenhuma empresa.")
    else:
        id_empresa_filtro, _ = selecionar_empresa_filtro(empresas_acessiveis, key="empresa_dre")
        totais = db.buscar_totais_dre(conn, id_empresa=id_empresa_filtro)

        if not totais:
            st.info("Ainda não há lançamentos suficientes para montar a DRE.")
        else:
            por_mes: dict[str, dict[str, float]] = {}
            for linha in totais:
                por_mes.setdefault(linha["ano_mes"], {})[linha["raiz_codigo"]] = linha["total"]

            meses_ordenados = sorted(por_mes.keys())

            linhas_dre = []
            for ano_mes in meses_ordenados:
                valores = por_mes[ano_mes]
                receitas_brutas = valores.get("1", 0.0)
                impostos = valores.get("4", 0.0)
                receita_liquida = receitas_brutas - impostos
                despesas = valores.get("2", 0.0) + valores.get("3", 0.0)
                resultado_liquido = receita_liquida - despesas

                linhas_dre.append(
                    {
                        "ano_mes": ano_mes,
                        "(+) Receitas Brutas": receitas_brutas,
                        "(-) Impostos": impostos,
                        "(=) Receita Líquida": receita_liquida,
                        "(-) Despesas (Fixas e Variáveis)": despesas,
                        "(=) Resultado Líquido": resultado_liquido,
                    }
                )

            df_dre = pd.DataFrame(linhas_dre).set_index("ano_mes").T
            df_dre.index.name = "DRE / Mês"

            ultimo_mes = meses_ordenados[-1]
            resultado_ultimo_mes = linhas_dre[-1]["(=) Resultado Líquido"]
            rotulo = "Lucro" if resultado_ultimo_mes >= 0 else "Prejuízo"
            st.metric(
                f"Resultado de {ultimo_mes} ({rotulo})",
                formatar_moeda(resultado_ultimo_mes),
            )

            if id_empresa_filtro is None:
                st.caption(
                    "⚠️ Visão consolidada de todas as empresas — útil como panorama, "
                    "mas não é um DRE válido pra declarar nada. Selecione uma empresa "
                    "específica pra isso."
                )

            st.subheader("Evolução mensal")
            st.dataframe(df_dre.map(formatar_moeda), width="stretch")

# =======================================================================
# ABA DASHBOARD
# =======================================================================
else:
    st.header("Dashboard Financeiro")

    if not empresas_acessiveis:
        st.warning("Você ainda não tem acesso a nenhuma empresa.")
    else:
        id_empresa_filtro, _ = selecionar_empresa_filtro(empresas_acessiveis, key="empresa_dashboard")
        meses = db.listar_meses_disponiveis(conn, id_empresa=id_empresa_filtro)

        if not meses:
            st.info("Ainda não há lançamentos para montar o dashboard.")
        else:
            mes_selecionado = st.selectbox("Mês de referência", meses, index=len(meses) - 1)

            totais = db.buscar_totais_dre(conn, id_empresa=id_empresa_filtro)
            por_mes: dict[str, dict[str, float]] = {}
            for linha in totais:
                por_mes.setdefault(linha["ano_mes"], {})[linha["raiz_codigo"]] = linha["total"]

            def receitas_despesas_saldo(ano_mes: str):
                valores = por_mes.get(ano_mes, {})
                receitas = valores.get("1", 0.0)
                despesas = valores.get("2", 0.0) + valores.get("3", 0.0) + valores.get("4", 0.0)
                saldo = receitas - despesas
                return receitas, despesas, saldo

            receitas_mes, despesas_mes, saldo_mes = receitas_despesas_saldo(mes_selecionado)

            col1, col2, col3 = st.columns(3)
            col1.metric("Total de Receitas", formatar_moeda(receitas_mes))
            col2.metric("Total de Despesas", formatar_moeda(despesas_mes))
            col3.metric(
                "Saldo do Mês", formatar_moeda(saldo_mes),
                delta=formatar_moeda(saldo_mes),
                delta_color="normal" if saldo_mes >= 0 else "inverse",
            )

            st.subheader("Receitas vs Despesas — mês a mês")
            meses_ordenados = sorted(por_mes.keys())
            dados_evolucao = [
                {
                    "Mês": am,
                    "Receitas": receitas_despesas_saldo(am)[0],
                    "Despesas": receitas_despesas_saldo(am)[1],
                }
                for am in meses_ordenados
            ]
            df_evolucao = pd.DataFrame(dados_evolucao).melt(
                id_vars="Mês", value_vars=["Receitas", "Despesas"],
                var_name="Tipo", value_name="Valor",
            )
            fig_barras = px.bar(
                df_evolucao, x="Mês", y="Valor", color="Tipo", barmode="group",
                color_discrete_map={"Receitas": "#2E7D32", "Despesas": "#C62828"},
            )
            st.plotly_chart(fig_barras, width="stretch")

            st.subheader(f"Distribuição das Despesas por Categoria — {mes_selecionado}")
            despesas_categoria = db.buscar_despesas_por_categoria(
                conn, mes_selecionado, id_empresa=id_empresa_filtro
            )
            if despesas_categoria:
                df_pizza = pd.DataFrame(despesas_categoria, columns=despesas_categoria[0].keys())
                fig_pizza = px.pie(df_pizza, names="categoria", values="total", hole=0.4)
                st.plotly_chart(fig_pizza, width="stretch")
            else:
                st.info(f"Não há despesas registradas em {mes_selecionado}.")
