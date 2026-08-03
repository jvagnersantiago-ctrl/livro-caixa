-- =====================================================================
-- LIVRO CAIXA - ESTRUTURA DE BANCO DE DADOS (SQLite)
-- =====================================================================
-- Ordem de criação importa por causa das Foreign Keys.
-- =====================================================================

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------
-- 1. CLIENTES
-- Nenhum campo obrigatório exceto o ID interno.
-- CPF tem UNIQUE, mas SQLite permite múltiplos NULLs em coluna UNIQUE,
-- então cadastros sem CPF não conflitam entre si.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS Clientes (
    id_cliente      INTEGER PRIMARY KEY AUTOINCREMENT,
    nome            TEXT,
    cpf             TEXT UNIQUE,
    telefone        TEXT,
    email           TEXT,
    observacoes     TEXT
);

-- ---------------------------------------------------------------------
-- 2. PLANO DE CONTAS
-- Hierarquia via auto-relacionamento (id_pai aponta pra própria tabela).
-- 'codigo' guarda a numeração (1, 1.1, 2.1.1...) só para exibição/ordenação;
-- quem garante a hierarquia de verdade é id_pai, não o texto do código.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS Plano_Contas (
    id_conta        INTEGER PRIMARY KEY AUTOINCREMENT,
    codigo          TEXT NOT NULL UNIQUE,
    nome            TEXT NOT NULL,
    id_pai          INTEGER,
    natureza        TEXT NOT NULL CHECK (natureza IN ('Receita', 'Despesa')),
    FOREIGN KEY (id_pai) REFERENCES Plano_Contas(id_conta)
);

-- ---------------------------------------------------------------------
-- 3. LANÇAMENTOS (Fluxo de Caixa)
-- Campo 'tipo' adicionado além do pedido original — ver explicação no chat.
-- CHECK (valor > 0): valor é sempre positivo, o sinal vem do 'tipo'.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS Lancamentos (
    id_lancamento       INTEGER PRIMARY KEY AUTOINCREMENT,
    data                TEXT NOT NULL,              -- formato 'YYYY-MM-DD'
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

CREATE INDEX IF NOT EXISTS idx_lancamentos_data   ON Lancamentos(data);
CREATE INDEX IF NOT EXISTS idx_lancamentos_conta   ON Lancamentos(id_conta_plano);
CREATE INDEX IF NOT EXISTS idx_lancamentos_cliente ON Lancamentos(id_cliente);

-- ---------------------------------------------------------------------
-- 4. CONTAS A PAGAR / RECEBER
-- 'status' é gravado manualmente (Pendente/Pago), mas "Atrasado" não deve
-- ser confiado direto dessa coluna — use a VIEW abaixo para relatórios.
-- ---------------------------------------------------------------------
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

-- ---------------------------------------------------------------------
-- VIEW: status real de Contas_Pagar_Receber calculado na hora da consulta,
-- não confiando na coluna 'status' para o caso "Atrasado".
-- ---------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS vw_contas_pagar_receber_status_real AS
SELECT
    c.*,
    CASE
        WHEN c.status = 'Pago' THEN 'Pago'
        WHEN c.status = 'Pendente' AND date(c.data_vencimento) < date('now') THEN 'Atrasado'
        ELSE c.status
    END AS status_real
FROM Contas_Pagar_Receber c;
