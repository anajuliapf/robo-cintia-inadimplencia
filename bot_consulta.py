"""
Robô Cíntia — Bot de consulta no Slack.

Responde a mensagens no formato:
    Orçamento CNPJ 12345678000199 - 01/06/2025 a 30/06/2025
    Extrato CNPJ 12345678000199 - 01/06/2025 a 30/06/2025

e devolve os orçamentos autorizados ou os depósitos PIX das seguradoras
no período, a partir das bases no Google Sheets (alimentadas pelo Metabase).

Variáveis de ambiente (ver .env.example):
    SLACK_BOT_TOKEN, SLACK_SIGNING_SECRET,
    SHEET_ORCAMENTO_CSV_URL, SHEET_EXTRATO_CSV_URL
"""

import os
import re
import unicodedata

import pandas as pd
from slack_bolt import App

app = App(
    token=os.environ["SLACK_BOT_TOKEN"],
    signing_secret=os.environ["SLACK_SIGNING_SECRET"],
)

df_orcamento = pd.read_csv(os.environ["SHEET_ORCAMENTO_CSV_URL"])
df_extrato = pd.read_csv(os.environ["SHEET_EXTRATO_CSV_URL"])

TIPOS_VALIDOS = ["Vistoria Autorizada Final", "Autorizado", "Realizado", "Reparo Autorizado"]
SEGURADORAS_VALIDAS = ["Allianz Seguros", "Arval Brasil", "SURA S/A", "Zurich", "Zurich Frotas", "Tokio Marine"]
PAGADORES_VALIDOS = [
    "ALLIANZ SEGUROS SA", "ALLIANZ SEGUROS S A",
    "ARVAL BRASIL", "ARVAL BRASIL LTDA",
    "SEGUROS SURA S/A", "Zurich Minas Brasil Seguros S/",
    "TOKIO MARINE SEGURADORA S A",
]

COMANDO_REGEX = re.compile(
    r"(orcamento|extrato)\s+cnpj\s+(\d{14})\s*-\s*(\d{2}/\d{2}/\d{4})\s*a\s*(\d{2}/\d{2}/\d{4})"
)
MSG_SEM_REGISTRO = "❌ Nenhum registro encontrado para o período e CNPJs selecionados."
MSG_FORMATO_INVALIDO = (
    "❌ Formato inválido. Use algo como:\n"
    "`Orçamento CNPJ 12345678000199 - 01/06/2025 a 30/06/2025`\n ou \n"
    "`Extrato CNPJ 12345678000199 - 01/06/2025 a 30/06/2025`"
)


def remover_acentos(texto):
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def formatar_cnpj(cnpj_raw):
    cnpj = "".join(filter(str.isdigit, str(cnpj_raw)))
    if len(cnpj) != 14:
        return cnpj_raw
    return f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"


def formatar_reais(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def filtrar_periodo(df, coluna_data, data_inicio, data_fim, dayfirst=False):
    df[coluna_data] = pd.to_datetime(df[coluna_data], errors="coerce", dayfirst=dayfirst)
    if data_inicio:
        df = df[df[coluna_data] >= pd.to_datetime(data_inicio, dayfirst=True)]
    if data_fim:
        df = df[df[coluna_data] <= pd.to_datetime(data_fim, dayfirst=True)]
    return df


def gerar_mensagem_orcamento(df, cnpjs=None, data_inicio=None, data_fim=None):
    df = df.copy()
    df["cnpj_normalizado"] = df["workshop_document_identifier"].astype(str).str.replace(r"\D", "", regex=True)
    if cnpjs:
        df = df[df["cnpj_normalizado"].isin(cnpjs)]
    df = filtrar_periodo(df, "conclusion_date", data_inicio, data_fim, dayfirst=True)
    df = df[df["conclusion_type_title"].isin(TIPOS_VALIDOS)]
    df = df[df["insurer_trade"].isin(SEGURADORAS_VALIDAS)]

    if df.empty:
        return MSG_SEM_REGISTRO

    mensagem = f"📅 *Orçamento Período:* {data_inicio} a {data_fim}\n\n"
    for _, row in df.iterrows():
        mensagem += (
            f"• CNPJ: {formatar_cnpj(row['workshop_document_identifier'])}"
            f" | Placa: {row.get('license_plate', '-')}"
            f" | Seguradora: {row.get('insurer_trade', '-')}"
            f" | Status: {row.get('conclusion_type_title', '-')}"
            f" | Data: {row['conclusion_date']:%d/%m/%Y}"
            f" | Valor: {formatar_reais(row['valor_liquido'])}\n"
        )
    return mensagem.strip()


def gerar_mensagem_extrato(df, cnpjs=None, data_inicio=None, data_fim=None):
    df = df.copy()
    df["cnpj_normalizado"] = df["document_identifier"].astype(str).str.replace(r"\D", "", regex=True)
    if cnpjs:
        df = df[df["cnpj_normalizado"].isin(cnpjs)]
    df = filtrar_periodo(df, "transacted_at", data_inicio, data_fim)
    df = df[df["transaction_type_translated"] == "Depósito de PIX"]
    df = df[df["payer_name"].isin(PAGADORES_VALIDOS)]

    if df.empty:
        return MSG_SEM_REGISTRO

    mensagem = f"📅 *Extrato Período:* {data_inicio} a {data_fim}\n\n"
    for _, row in df.iterrows():
        mensagem += (
            f"• CNPJ: {formatar_cnpj(row['document_identifier'])}"
            f" | Pagador: {row.get('payer_name', '-')}"
            f" | Tipo: {row.get('transaction_type_translated', '-')}"
            f" | Data: {row['transacted_at']:%d/%m/%Y}"
            f" | Valor: {formatar_reais(row['transaction_amount'])}\n"
        )
    return mensagem.strip()


@app.message("")  # captura qualquer mensagem
def responder(message, say):
    texto = remover_acentos(message.get("text", "")).lower()
    match = COMANDO_REGEX.search(texto)

    if not match:
        say(MSG_FORMATO_INVALIDO)
        return

    comando, cnpj, data_inicio, data_fim = match.groups()
    if comando == "orcamento":
        resposta = gerar_mensagem_orcamento(df_orcamento, [cnpj], data_inicio, data_fim)
    else:
        resposta = gerar_mensagem_extrato(df_extrato, [cnpj], data_inicio, data_fim)
    say(resposta)


if __name__ == "__main__":
    app.start(port=3000)
