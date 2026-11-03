"""
Robô Cíntia — Alerta diário de clientes em risco de inadimplência.

Lê a planilha de acompanhamento das operações (Google Sheets, alimentada pelo
Metabase), classifica cada operação em Verde / Moderado / Vermelho e envia
um resumo por DM no Slack.

Variáveis de ambiente (ver .env.example):
    SLACK_BOT_TOKEN, SHEET_RISCO_CSV_URL, SLACK_EMAIL_DESTINO
"""

import os

import pandas as pd
from slack_sdk import WebClient
from tabulate import tabulate

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SHEET_RISCO_CSV_URL = os.environ["SHEET_RISCO_CSV_URL"]
SLACK_EMAIL_DESTINO = os.environ["SLACK_EMAIL_DESTINO"]

client = WebClient(token=SLACK_BOT_TOKEN)

# Colunas da planilha de acompanhamento
COL_CNPJ = "Unnamed: 0"
COL_NOME = "Unnamed: 1"
COL_OPERACAO = "Unnamed: 2"
COL_VALOR_PAGO_PARCELA = "Unnamed: 7"
COL_PCT_PAGO = "Unnamed: 8"
COL_PRAZO = "Unnamed: 10"
COL_PCT_TEMPO_CORRIDO = "Unnamed: 12"


def enviar_mensagem_por_email(email, mensagem):
    """Envia uma DM no Slack para o usuário dono do e-mail informado."""
    try:
        user_id = client.users_lookupByEmail(email=email)["user"]["id"]
        dm_channel = client.conversations_open(users=[user_id])["channel"]["id"]
        response = client.chat_postMessage(channel=dm_channel, text=mensagem)
        print("Mensagem enviada com sucesso?", response["ok"])
    except Exception as e:
        print(f"Erro ao enviar mensagem: {e}")


def carregar_operacoes(url):
    """Lê a planilha e mantém apenas as linhas com percentuais válidos."""
    df = pd.read_csv(url)

    df[COL_PCT_TEMPO_CORRIDO] = df[COL_PCT_TEMPO_CORRIDO].astype(str).str.strip()
    df[COL_PCT_PAGO] = df[COL_PCT_PAGO].astype(str).str.strip()

    df = df[df[COL_PCT_TEMPO_CORRIDO].str.match(r"^\d{1,3},?\d*%$")].copy()

    df[COL_PCT_TEMPO_CORRIDO] = (
        df[COL_PCT_TEMPO_CORRIDO].str.replace("%", "", regex=False).str.replace(",", ".", regex=False).astype(float)
    )
    df[COL_PCT_PAGO] = df[COL_PCT_PAGO].str.replace("%", "", regex=False).str.replace(",", ".", regex=False)
    df = df[pd.to_numeric(df[COL_PCT_PAGO], errors="coerce").notnull()]
    df[COL_PCT_PAGO] = df[COL_PCT_PAGO].astype(float)

    df[COL_VALOR_PAGO_PARCELA] = pd.to_numeric(df[COL_VALOR_PAGO_PARCELA], errors="coerce").fillna(0)
    df[COL_PRAZO] = pd.to_numeric(df[COL_PRAZO], errors="coerce").fillna(0)
    return df


def classificar_status(row):
    """Regra principal: compara o % pago com o % de tempo corrido da operação."""
    tempo_corrido = row[COL_PCT_TEMPO_CORRIDO]
    pago = row[COL_PCT_PAGO]

    if tempo_corrido >= 71 and pago < tempo_corrido:
        return "Vermelho"
    elif 21 <= tempo_corrido <= 70 and (tempo_corrido - 15) <= pago < tempo_corrido:
        return "Moderado"
    elif 71 <= tempo_corrido <= 100 and pago >= tempo_corrido:
        return "Verde"
    elif 21 <= tempo_corrido <= 70 and pago >= tempo_corrido:
        return "Verde"
    elif 0 <= tempo_corrido <= 20 and pago >= 0:
        return "Verde"
    return ""


def completar_status(row):
    """Regra secundária: usa o valor pago da parcela e o prazo quando a regra principal não decide."""
    if row["Status"] != "":
        return row["Status"]

    valor_pago_parcela = row[COL_VALOR_PAGO_PARCELA]
    prazo = row[COL_PRAZO]

    if valor_pago_parcela > 0 and prazo >= 22:
        return "Verde"
    elif valor_pago_parcela == 0 and 15 < prazo <= 30:
        return "Moderado"
    elif valor_pago_parcela > 0 and prazo < 22:
        return "Moderado"
    elif valor_pago_parcela == 0 and prazo <= 15:
        return "Vermelho"
    return ""


def formatar_cnpj(cnpj_raw):
    cnpj = "".join(filter(str.isdigit, str(cnpj_raw))).zfill(14)
    if len(cnpj) != 14:
        return str(cnpj_raw)
    return f"{cnpj[:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:]}"


def gerar_mensagem_risco(df):
    mensagem = "📊 *Atualização diária dos clientes em risco:*\n\n"
    for status, emoji in [("Vermelho", ":red_circle:"), ("Moderado", ":orange_circle:")]:
        grupo = df[df["Status"] == status]
        if grupo.empty:
            continue
        mensagem += f"{emoji} *{status}* ({len(grupo)} clientes):\n"
        for _, row in grupo.iterrows():
            cnpj = formatar_cnpj(row.get(COL_CNPJ, ""))
            nome = str(row.get(COL_NOME, "")).strip()
            operacao = str(row.get(COL_OPERACAO, "")).strip()
            pct_pago = f"{row[COL_PCT_PAGO]:.1f}%"
            prazo = int(row[COL_PRAZO]) if pd.notnull(row[COL_PRAZO]) else "-"
            mensagem += f"• CNPJ {cnpj} – {nome} – Operação: {operacao} – {pct_pago} pago – Prazo {prazo} dias\n"
        mensagem += "\n"
    return mensagem.strip()


def main():
    df = carregar_operacoes(SHEET_RISCO_CSV_URL)
    df["Status"] = df.apply(classificar_status, axis=1)
    vazios = df["Status"] == ""
    df.loc[vazios, "Status"] = df[vazios].apply(completar_status, axis=1)

    df_risco = df[df["Status"].isin(["Vermelho", "Moderado"])].copy()
    mensagem = gerar_mensagem_risco(df_risco)

    enviar_mensagem_por_email(SLACK_EMAIL_DESTINO, mensagem)
    print(tabulate(df_risco, headers="keys", tablefmt="grid"))
    print(mensagem)


if __name__ == "__main__":
    main()
