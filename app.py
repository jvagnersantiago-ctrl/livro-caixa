"""
Interface Streamlit para o Livro Caixa.
Rodar com: streamlit run app.py
Requer que o schema já esteja criado no Supabase (schema_postgres.sql,
rodado uma vez no SQL Editor) e as credenciais em .streamlit/secrets.toml.
"""

import hashlib
from datetime import date

import pandas as pd
import plotly.express as px
import streamlit as st
from postgrest import APIError

import db

st.set_page_config(page_title="Livro Caixa", page_icon="💰", layout="wide")


# =======================================================================
# AUTENTICAÇÃO
# =======================================================================
# Credenciais fixas pra início. As senhas ficam como HASH (SHA-256), não
# em texto puro — mesmo assim, isso é autenticação básica, não segurança
# de produção. Antes de publicar online com dados reais de cliente,
# mova isso pra st.secrets (secrets.toml, fora do controle de versão).

def _hash_senha(senha: str) -> str:
    return hashlib.sha256(senha.encode("utf-8")).hexdigest()


USUARIOS = {
    "admin": _hash_senha("admin123"),
    # TROCAR ANTES DE USAR: nome de usuário e senha reais da sua esposa.
    "usuario_2": _hash_senha("troque-esta-senha"),
}


def tela_login() -> None:
    st.title("🔒 Livro Caixa")
    st.caption("Entre com seu usuário e senha para continuar.")
    with st.form("form_login"):
        usuario = st.text_input("Usuário")
        senha = st.text_input("Senha", type="password")
        entrar = st.form_submit_button("Entrar")

        if entrar:
            hash_informado = _hash_senha(senha)
            if usuario in USUARIOS and USUARIOS[usuario] == hash_informado:
                st.session_state["autenticado"] = True
                st.session_state["usuario_logado"] = usuario
                st.rerun()
            else:
                st.error("Usuário ou senha incorretos.")


st.session_state.setdefault("autenticado", False)
st.session_state.setdefault("usuario_logado", None)

if not st.session_state["autenticado"]:
    # Nada abaixo desta linha roda sem login: nem menu, nem conexão
    # com o banco, nem qualquer dado.
    tela_login()
    st.stop()

# ---- A partir daqui, o usuário já está autenticado ----

# Não existe mais um "inicializar_banco()" automático: a API REST do
# Supabase não roda CREATE TABLE. O schema já foi criado uma vez direto
# no SQL Editor (schema_postgres.sql) — se as tabelas não existirem lá,
# é isso que precisa ser rodado, não algo que o app possa resolver sozinho.
conn = db.get_connection()

FORMAS_PAGAMENTO = [
    "Pix", "Dinheiro", "Cartão de Crédito", "Cartão de Débito",
    "Transferência", "Boleto", "Outro",
]


def formatar_moeda(valor: float) -> str:
    texto = f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"


st.sidebar.markdown(f"👤 Logado como **{st.session_state['usuario_logado']}**")
if st.sidebar.button("Sair"):
    st.session_state["autenticado"] = False
    st.session_state["usuario_logado"] = None
    st.rerun()
st.sidebar.markdown("---")

pagina = st.sidebar.radio(
    "Navegação", ["Clientes", "Lançamentos", "Plano de Contas", "DRE", "Dashboard"]
)

# =======================================================================
# ABA CLIENTES
# =======================================================================
if pagina == "Clientes":
    st.header("Clientes")

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
                    # O banco permite nome nulo, mas na prática um cliente
                    # sem nome não serve pra nada em nenhum relatório —
                    # essa validação é só de interface.
                    st.error("Informe o nome do cliente.")
                else:
                    try:
                        db.inserir_cliente(
                            conn,
                            nome.strip(),
                            cpf.strip() or None,
                            telefone.strip() or None,
                            email.strip() or None,
                            observacoes.strip() or None,
                        )
                        st.success(f"Cliente '{nome}' cadastrado.")
                        st.rerun()
                    except APIError:
                        st.error("Já existe um cliente cadastrado com esse CPF.")

    st.subheader("Clientes cadastrados")
    clientes = db.listar_clientes(conn)

    if not clientes:
        st.info("Nenhum cliente cadastrado ainda.")
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

        # --- Formulário de edição, se algum cliente foi selecionado ---
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
                                conn,
                                registro["id_cliente"],
                                nome_e.strip(),
                                cpf_e.strip() or None,
                                telefone_e.strip() or None,
                                email_e.strip() or None,
                                observacoes_e.strip() or None,
                            )
                            st.session_state["cliente_editando"] = None
                            st.success("Cliente atualizado.")
                            st.rerun()
                        except APIError:
                            st.error("Já existe outro cliente cadastrado com esse CPF.")
                if cancelar:
                    st.session_state["cliente_editando"] = None
                    st.rerun()

        # --- Confirmação de exclusão, se algum cliente foi selecionado ---
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
                except APIError:
                    st.error(
                        "Não é possível excluir: este cliente tem lançamentos "
                        "ou contas a pagar/receber vinculados a ele."
                    )
            if col_cancelar.button("Cancelar"):
                st.session_state["cliente_excluindo"] = None
                st.rerun()

# =======================================================================
# ABA LANÇAMENTOS
# =======================================================================
elif pagina == "Lançamentos":
    st.header("Lançamentos (Fluxo de Caixa)")

    clientes = db.listar_clientes(conn)
    plano_contas = db.listar_plano_contas_folhas(conn)

    if not plano_contas:
        st.warning(
            "O Plano de Contas está vazio. Cadastre pelo menos uma conta-folha "
            "na aba 'Plano de Contas' antes de lançar movimentações."
        )
    else:
        opcoes_cliente = {"Nenhum": None}
        opcoes_cliente.update(
            {f"{c['nome']} ({c['cpf'] or 'sem CPF'})": c["id_cliente"] for c in clientes}
        )
        # Só contas-folha aparecem aqui — vincular a uma conta-pai quebra
        # a soma hierárquica usada na DRE e no Dashboard.
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
                        conn,
                        data=data_lanc.isoformat(),
                        tipo=tipo,
                        valor=valor,
                        forma_pagamento=forma_pagamento,
                        descricao=descricao.strip() or None,
                        id_cliente=opcoes_cliente[cliente_sel],
                        id_conta_plano=opcoes_categoria[categoria_sel],
                    )
                    st.success("Lançamento registrado.")
                    st.rerun()

    st.subheader("Histórico de Lançamentos")
    lancamentos = db.listar_lancamentos(conn)

    if not lancamentos:
        st.info("Nenhum lançamento registrado ainda.")
    elif not plano_contas:
        # Sem plano de contas não dá pra montar os dropdowns de edição.
        df = pd.DataFrame(lancamentos, columns=lancamentos[0].keys())
        st.dataframe(df, width="stretch", hide_index=True)
    else:
        st.session_state.setdefault("lancamento_editando", None)
        st.session_state.setdefault("lancamento_excluindo", None)

        cabecalho = st.columns([1.5, 1, 1.3, 1.5, 2.5, 2, 1, 1])
        titulos = ["Data", "Tipo", "Valor", "Forma Pgto.", "Categoria", "Cliente", "", ""]
        for col, titulo in zip(cabecalho, titulos):
            col.markdown(f"**{titulo}**")

        for l in lancamentos:
            linha = st.columns([1.5, 1, 1.3, 1.5, 2.5, 2, 1, 1])
            linha[0].write(l["data"])
            linha[1].write(l["tipo"])
            linha[2].write(formatar_moeda(l["valor"]))
            linha[3].write(l["forma_pagamento"] or "—")
            linha[4].write(l["categoria"])
            linha[5].write(l["cliente"] or "—")
            if linha[6].button("✏️", key=f"editar_lanc_{l['id']}"):
                st.session_state["lancamento_editando"] = l["id"]
                st.session_state["lancamento_excluindo"] = None
                st.rerun()
            if linha[7].button("🗑️", key=f"excluir_lanc_{l['id']}"):
                st.session_state["lancamento_excluindo"] = l["id"]
                st.session_state["lancamento_editando"] = None
                st.rerun()

        # --- Formulário de edição ---
        if st.session_state["lancamento_editando"] is not None:
            registro = db.buscar_lancamento_por_id(conn, st.session_state["lancamento_editando"])

            id_para_label_cliente = {v: k for k, v in opcoes_cliente.items()}
            id_para_label_categoria = {v: k for k, v in opcoes_categoria.items()}
            label_cliente_atual = id_para_label_cliente.get(registro["id_cliente"], "Nenhum")
            label_categoria_atual = id_para_label_categoria.get(registro["id_conta_plano"])

            st.markdown("---")
            st.subheader(f"Editando lançamento #{registro['id_lancamento']}")

            if label_categoria_atual is None:
                st.warning(
                    "A categoria original deste lançamento não está mais disponível "
                    "como conta-folha (o Plano de Contas mudou). Escolha uma nova "
                    "categoria ao salvar."
                )
                indice_categoria = 0
            else:
                indice_categoria = list(opcoes_categoria.keys()).index(label_categoria_atual)

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
                    "Cliente", list(opcoes_cliente.keys()),
                    index=list(opcoes_cliente.keys()).index(label_cliente_atual),
                )
                categoria_sel_edit = col2.selectbox(
                    "Categoria", list(opcoes_categoria.keys()), index=indice_categoria
                )
                descricao_edit = st.text_input("Descrição", value=registro["descricao"] or "")

                col_salvar, col_cancelar = st.columns(2)
                salvar = col_salvar.form_submit_button("Salvar Alterações")
                cancelar = col_cancelar.form_submit_button("Cancelar")

                if salvar:
                    db.atualizar_lancamento(
                        conn,
                        id_lancamento=registro["id_lancamento"],
                        data=data_edit.isoformat(),
                        tipo=tipo_edit,
                        valor=valor_edit,
                        forma_pagamento=forma_pagamento_edit,
                        descricao=descricao_edit.strip() or None,
                        id_cliente=opcoes_cliente[cliente_sel_edit],
                        id_conta_plano=opcoes_categoria[categoria_sel_edit],
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

    todas_contas = db.listar_todas_contas(conn)

    st.subheader("Árvore atual")
    if not todas_contas:
        st.info("Nenhuma conta cadastrada ainda.")
    else:
        # Monta a árvore em memória (id -> filhos) e imprime por
        # indentação, ordenado pelo código.
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
                # Conta raiz nova: natureza precisa ser escolhida.
                natureza_sel = st.radio(
                    "Natureza", ["Receita", "Despesa"], horizontal=True
                )
            else:
                # Subconta herda a natureza do pai — evita uma subconta de
                # Despesas nascer classificada como Receita por engano.
                natureza_sel = next(
                    c["natureza"] for c in todas_contas if c["id_conta"] == id_pai_sel
                )
                st.caption(f"Natureza herdada da conta pai: **{natureza_sel}**")

            criar = st.form_submit_button("Criar Conta")
            if criar:
                if not nome_conta.strip():
                    st.error("Informe o nome da conta.")
                else:
                    db.inserir_conta(conn, nome_conta.strip(), id_pai_sel, natureza_sel)
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
                "⚠️ Contas com subcontas ou com lançamentos/contas a pagar "
                "vinculados não podem ser excluídas — o banco bloqueia "
                "automaticamente pra proteger seu histórico financeiro."
            )
            if st.button("Excluir Conta Selecionada"):
                id_conta_excluir = opcoes_excluir[conta_excluir_sel]
                try:
                    db.excluir_conta(conn, id_conta_excluir)
                    st.success("Conta excluída.")
                    st.rerun()
                except APIError:
                    st.error(
                        "Não é possível excluir: esta conta tem subcontas ou "
                        "lançamentos/contas a pagar vinculados a ela."
                    )

# =======================================================================
# ABA DRE
# =======================================================================
elif pagina == "DRE":
    st.header("DRE — Demonstração do Resultado do Exercício")

    totais = db.buscar_totais_dre(conn)

    if not totais:
        st.info("Ainda não há lançamentos suficientes para montar a DRE.")
    else:
        # Reorganiza os totais em: {ano_mes: {raiz_codigo: total}}
        por_mes: dict[str, dict[str, float]] = {}
        for linha in totais:
            por_mes.setdefault(linha["ano_mes"], {})[linha["raiz_codigo"]] = linha["total"]

        meses_ordenados = sorted(por_mes.keys())

        # Códigos raiz definidos no plano de contas padrão inserido em
        # criar_banco.py: 1=Receitas, 2=Despesas Fixas,
        # 3=Despesas Variáveis, 4=Impostos.
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

        st.subheader("Evolução mensal")
        st.dataframe(df_dre.map(formatar_moeda), width="stretch")

# =======================================================================
# ABA DASHBOARD
# =======================================================================
else:
    st.header("Dashboard Financeiro")

    meses = db.listar_meses_disponiveis(conn)

    if not meses:
        st.info("Ainda não há lançamentos para montar o dashboard.")
    else:
        mes_selecionado = st.selectbox("Mês de referência", meses, index=len(meses) - 1)

        # Reaproveita a mesma agregação da DRE — assim "Total de Despesas"
        # aqui é exatamente igual a "(-) Despesas" + "(-) Impostos" da DRE,
        # e "Saldo" bate com o "(=) Resultado Líquido".
        totais = db.buscar_totais_dre(conn)
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
            "Saldo do Mês",
            formatar_moeda(saldo_mes),
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
        despesas_categoria = db.buscar_despesas_por_categoria(conn, mes_selecionado)
        if despesas_categoria:
            df_pizza = pd.DataFrame(despesas_categoria, columns=despesas_categoria[0].keys())
            fig_pizza = px.pie(df_pizza, names="categoria", values="total", hole=0.4)
            st.plotly_chart(fig_pizza, width="stretch")
        else:
            st.info(f"Não há despesas registradas em {mes_selecionado}.")
