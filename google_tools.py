
"""Integração com Google Calendar, Google Meet e Google Sheets."""

import os
import uuid
import unicodedata

from datetime import datetime

from dotenv import load_dotenv

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


# Carregar variáveis de ambiente
load_dotenv()


# Escopos de autenticação Google
SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/spreadsheets",
]


# Identificar possíveis nomes dos cabeçalhos
CAMPOS_DA_PLANILHA = {
    "nome": {"nome", "nomecandidato"},
    "email": {"email", "emailcandidato"},
    "vaga": {"vaga", "cargo"},
    "data": {"data", "dataentrevista"},
    "hora": {"hora", "horario", "horarioentrevista"},
    "status": {"status", "situacao"},
}


# AUTENTICAÇÃO GOOGLE

def autenticar_google():
    """Autentica a conta Google e retorna as credenciais."""

    credenciais = None

    if os.path.exists("token.json"):
        credenciais = Credentials.from_authorized_user_file(
            "token.json",
            SCOPES,
        )

    if (
        credenciais
        and credenciais.expired
        and credenciais.refresh_token
    ):
        try:
            credenciais.refresh(Request())

        except RefreshError as erro:
            if "invalid_grant" in str(erro):
                raise

            credenciais = None

    if not credenciais or not credenciais.valid:

        fluxo = InstalledAppFlow.from_client_secrets_file(
            "credentials.json",
            SCOPES,
        )

        credenciais = fluxo.run_local_server(port=0)

    with open("token.json", "w", encoding="utf-8") as arquivo_token:
        arquivo_token.write(credenciais.to_json())

    return credenciais


# AGENDAR ENTREVISTA COM GOOGLE MEET

def agendar_entrevista_com_meet(
    nome_candidato,
    email_candidato,
    data_hora_inicio,
    data_hora_fim,
):
    """Cria evento no Google Calendar com link do Meet."""

    inicio = datetime.fromisoformat(data_hora_inicio)
    fim = datetime.fromisoformat(data_hora_fim)

    if fim <= inicio:
        raise ValueError(
            "O horário de término deve ser posterior ao início."
        )

    servico = build(
        "calendar",
        "v3",
        credentials=autenticar_google(),
    )

    evento = {
        "summary": f"Entrevista com {nome_candidato}",

        "description": (
            "Entrevista de alinhamento com o time de People."
        ),

        "start": {
            "dateTime": data_hora_inicio,
            "timeZone": "America/Sao_Paulo",
        },

        "end": {
            "dateTime": data_hora_fim,
            "timeZone": "America/Sao_Paulo",
        },

        "attendees": [
            {"email": email_candidato},
        ],

        "conferenceData": {
            "createRequest": {
                "requestId": str(uuid.uuid4()),
                "conferenceSolutionKey": {
                    "type": "hangoutsMeet",
                },
            },
        },
    }

    evento_criado = servico.events().insert(
        calendarId="primary",
        body=evento,
        conferenceDataVersion=1,
        sendUpdates="all",
    ).execute()

    return {
        "evento_id": evento_criado.get("id"),
        "link_meet": evento_criado.get("hangoutLink"),
        "mensagem": "Entrevista criada no Google Calendar.",
    }


# NORMALIZAR CABEÇALHOS

def normalizar_cabecalho(texto):
    """Remove acentos, espaços e converte para minúsculas."""

    texto = unicodedata.normalize("NFKD", str(texto))

    texto = "".join(
        caractere
        for caractere in texto
        if not unicodedata.combining(caractere)
    )

    return texto.replace(" ", "").strip().lower()


# CONVERTER NÚMERO DA COLUNA PARA LETRA

def numero_para_coluna(numero):
    """Converte índice numérico em letra de coluna do Sheets."""

    resultado = ""

    while numero >= 0:
        resultado = chr(numero % 26 + 65) + resultado
        numero = numero // 26 - 1

    return resultado


# REGISTRAR CANDIDATO NA PLANILHA

def registrar_candidato_planilha(
    nome,
    email,
    vaga,
    status,
    data_hora_inicio=None,
    data_hora_fim=None,
    id_planilha=None,
):
    """Registra candidato na próxima linha disponível do Sheets."""

    # Obter ID da planilha
    id_planilha = (
        id_planilha
        or os.getenv("PLANILHA_ID")
    )

    if not id_planilha:
        raise ValueError(
            "PLANILHA_ID não encontrado no arquivo .env."
        )

    # Validar dados básicos
    if not nome or not email:
        raise ValueError(
            "Nome e e-mail do candidato são obrigatórios."
        )

    # Criar serviço Google Sheets
    servico = build(
        "sheets",
        "v4",
        credentials=autenticar_google(),
    )

    # Consultar abas da planilha
    planilha = servico.spreadsheets().get(
        spreadsheetId=id_planilha,
        fields="sheets(properties(sheetId,title))",
    ).execute()

    abas = planilha.get("sheets", [])

    if not abas:
        raise ValueError(
            "Nenhuma aba encontrada na planilha."
        )

    # Utilizar a primeira aba existente
    aba = abas[0]["properties"]
    nome_aba = aba["title"]

    # Buscar primeira linha
    intervalo = f"'{nome_aba}'!1:1"

    resposta = servico.spreadsheets().values().get(
        spreadsheetId=id_planilha,
        range=intervalo,
    ).execute()

    linhas = resposta.get("values", [])

    if not linhas:
        raise ValueError(
            "A primeira linha está vazia. "
            "Adicione os cabeçalhos: nome, email, vaga, data, hora, status."
        )

    cabecalho = linhas[0]

    # Identificar colunas
    colunas = {}

    for indice, nome_coluna in enumerate(cabecalho):

        nome_normalizado = normalizar_cabecalho(nome_coluna)

        for campo, alternativas in CAMPOS_DA_PLANILHA.items():

            if nome_normalizado in alternativas:
                colunas[campo] = indice

    # Verificar campos obrigatórios
    obrigatorios = {
        "nome",
        "email",
        "vaga",
        "status",
    }

    faltantes = obrigatorios - set(colunas)

    if faltantes:
        raise ValueError(
            "Cabeçalhos ausentes na planilha: "
            + ", ".join(sorted(faltantes))
        )

    # Preparar informações
    dados = {
        "nome": nome,
        "email": email,
        "vaga": vaga,
        "status": status,
    }

    # Preparar data e horário
    if data_hora_inicio:

        inicio = datetime.fromisoformat(data_hora_inicio)

        dados["data"] = inicio.strftime("%d/%m/%Y")

        hora = inicio.strftime("%H:%M")

        if data_hora_fim:

            fim = datetime.fromisoformat(data_hora_fim)

            if fim <= inicio:
                raise ValueError(
                    "O término da entrevista deve ser posterior ao início."
                )

            hora += " - " + fim.strftime("%H:%M")

        dados["hora"] = hora

    # Criar linha conforme os cabeçalhos existentes
    quantidade_colunas = max(
        max(colunas.values()) + 1,
        len(cabecalho),
    )

    valores = [""] * quantidade_colunas

    for campo, valor in dados.items():

        if campo in colunas:
            valores[colunas[campo]] = valor

    # Definir intervalo de gravação
    ultima_coluna = numero_para_coluna(
        quantidade_colunas - 1
    )

    intervalo_destino = (
        f"'{nome_aba}'!A:{ultima_coluna}"
    )

    # Inserir candidato na planilha
    resultado = servico.spreadsheets().values().append(
        spreadsheetId=id_planilha,
        range=intervalo_destino,
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={
            "values": [valores],
        },
    ).execute()

    atualizacao = resultado.get("updates", {})

    return {
        "mensagem": "Candidato registrado com sucesso.",
        "intervalo": atualizacao.get("updatedRange"),
        "linhas_registradas": atualizacao.get("updatedRows", 0),
    }
