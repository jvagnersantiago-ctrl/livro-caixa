"""
Cria/atualiza o banco de dados livro_caixa.db localmente.
Rodar: python criar_banco.py

Nota: o app.py agora chama db.inicializar_banco() sozinho a cada início,
então este script não é mais obrigatório pra rodar o sistema — ele
continua útil pra inspecionar/preparar o banco localmente antes de
subir pra nuvem.
"""

import os

import db


def main() -> None:
    banco_ja_existia = os.path.exists(db.DB_PATH)
    conn = db.get_connection()

    db.inicializar_banco(conn)

    (qtd_contas,) = conn.execute("SELECT COUNT(*) FROM Plano_Contas").fetchone()
    acao = "atualizado" if banco_ja_existia else "criado"
    print(f"Banco '{db.DB_PATH}' {acao} com sucesso.")
    print(f"Plano de Contas tem {qtd_contas} registros.")

    conn.close()


if __name__ == "__main__":
    main()
