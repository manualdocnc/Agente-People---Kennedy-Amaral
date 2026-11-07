
"""Agente People: interpretação, agendamento e registro de candidatos."""

import json
import os
import re
import hashlib
import tempfile

from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

from google_tools import (
    agendar_entrevista_com_meet,
    registrar_candidato_planilha,
)


# =====================================================
# 1. CONFIGURAÇÕES
# =====================================================

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
PLANILHA_ID = os.getenv("PLANILHA_ID")

MODELO_GROQ = "openai/gpt-oss-20b"

client = (
    OpenAI(
        api_key=GROQ_API_KEY,
        base_url="https://api.groq.com/openai/v1",
    )
    if GROQ_API_KEY
    else None
)

# Arquivo para controlar operações já realizadas.
ARQUIVO_CONTROLE = Path(__file__).with_name(
    "controle_agendamentos.json"
)


# =====================================================
# 2. INSTRUÇÕES PARA A GROQ
# =====================================================

PROMPT = """
Você é o Agente People, um assistente especializado em recrutamento.

Sua tarefa é interpretar solicitações de agendamento de entrevistas.

Extraia TODAS as informações presentes na mensagem do usuário.

Retorne exclusivamente um objeto JSON válido, sem explicações,
sem Markdown e sem blocos de código.

Use exatamente estas chaves:

{
    "nome": "",
    "email": "",
    "vaga": "",
    "status": "",
    "data_hora_inicio": "",
    "data_hora_fim": ""
}

REGRAS IMPORTANTES:

1. nome: nome completo do candidato.
2. email: endereço de e-mail do candidato.
3. vaga: cargo ou vaga pretendida.
4. status: situação do candidato.
5. data_hora_inicio: data e hora inicial em ISO 8601.
6. data_hora_fim: data e hora final em ISO 8601.

INTERPRETAÇÃO:

- "dia 15/11/2026" significa 2026-11-15.
- "às 7 horas" significa 07:00.
- "às 8 horas" significa 08:00.
- Quando o usuário disser "das 7 às 8", interprete como início
  às 07:00 e término às 08:00.
- Considere o fuso horário America/Sao_Paulo.
- Não invente nome, e-mail, vaga ou status.
- Não invente datas nem horários.
- Se uma informação não estiver presente, deixe o campo vazio.
- Preserve o status informado pelo usuário, inclusive "Em análise".
- Não confunda a vaga com o status.
- Não confunda o nome do candidato com o nome do recrutador.

A resposta deve conter somente o JSON.
"""


# =====================================================
# 3. CONTROLE DE OPERAÇÕES PARA EVITAR DUPLICIDADES
# =====================================================

def carregar_controle():
    """Carrega o histórico local de operações realizadas."""

    if not ARQUIVO_CONTROLE.exists():
        return {}

    try:
        with open(
            ARQUIVO_CONTROLE,
            "r",
            encoding="utf-8",
        ) as arquivo:
            dados = json.load(arquivo)

        return dados if isinstance(dados, dict) else {}

    except (json.JSONDecodeError, OSError):
        raise RuntimeError(
            "Não foi possível ler controle_agendamentos.json. "
            "Não execute novamente antes de verificar o arquivo."
        )


def salvar_controle(controle):
    """Salva o histórico de forma atômica."""

    diretorio = ARQUIVO_CONTROLE.parent

    fd, caminho_temporario = tempfile.mkstemp(
        dir=str(diretorio),
        suffix=".tmp",
    )

    try:
        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
        ) as arquivo:

            json.dump(
                controle,
                arquivo,
                ensure_ascii=False,
                indent=4,
            )

            arquivo.flush()
            os.fsync(arquivo.fileno())

        os.replace(
            caminho_temporario,
            ARQUIVO_CONTROLE,
        )

    finally:
        if os.path.exists(caminho_temporario):
            os.remove(caminho_temporario)


def gerar_chave_operacao(dados):
    """Gera identificador estável para evitar repetição."""

    identificador = "|".join([
        dados["email"].strip().lower(),
        dados["data_hora_inicio"],
        dados["data_hora_fim"],
    ])

    return hashlib.sha256(
        identificador.encode("utf-8")
    ).hexdigest()


# =====================================================
# 4. INTERPRETAR A SOLICITAÇÃO
# =====================================================

def interpretar_solicitacao(mensagem_usuario):
    """Solicita à Groq os dados estruturados do candidato."""

    resposta = client.chat.completions.create(
        model=MODELO_GROQ,
        messages=[
            {
                "role": "system",
                "content": PROMPT,
            },
            {
                "role": "user",
                "content": mensagem_usuario,
            },
        ],
        response_format={
            "type": "json_object",
        },
        temperature=0,
    )

    conteudo = resposta.choices[0].message.content

    if not conteudo:
        raise ValueError(
            "A Groq não retornou informações."
        )

    try:
        dados = json.loads(conteudo)

    except json.JSONDecodeError:
        raise ValueError(
            "A resposta da Groq não contém um JSON válido."
        )

    campos = [
        "nome",
        "email",
        "vaga",
        "status",
        "data_hora_inicio",
        "data_hora_fim",
    ]

    # Garantir que todos os campos existam.
    dados = {
        campo: str(dados.get(campo) or "").strip()
        for campo in campos
    }

    return dados


# =====================================================
# 5. VALIDAR OS DADOS
# =====================================================

def validar_dados(dados):
    """Verifica se todos os campos estão corretos."""

    obrigatorios = [
        "nome",
        "email",
        "vaga",
        "status",
        "data_hora_inicio",
        "data_hora_fim",
    ]

    faltantes = [
        campo
        for campo in obrigatorios
        if not dados.get(campo)
    ]

    if faltantes:
        nomes = {
            "nome": "nome",
            "email": "e-mail",
            "vaga": "vaga",
            "status": "status",
            "data_hora_inicio": "data e horário de início",
            "data_hora_fim": "data e horário de término",
        }

        return [
            nomes[campo]
            for campo in faltantes
        ]

    # Validar e-mail.
    padrao_email = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"

    if not re.match(padrao_email, dados["email"]):
        raise ValueError(
            "O e-mail informado não é válido."
        )

    # Validar datas.
    try:
        inicio = datetime.fromisoformat(
            dados["data_hora_inicio"]
        )

        fim = datetime.fromisoformat(
            dados["data_hora_fim"]
        )

    except ValueError:
        raise ValueError(
            "Data inválida. Utilize o formato "
            "YYYY-MM-DDTHH:MM:SS."
        )

    if inicio.tzinfo != fim.tzinfo:
        raise ValueError(
            "As datas de início e término precisam "
            "usar o mesmo padrão de fuso horário."
        )

    if fim <= inicio:
        raise ValueError(
            "O término precisa ser posterior ao início."
        )

    return []


# =====================================================
# 6. EXECUTAR O AGENTE
# =====================================================

def executar_agente_rh(mensagem_usuario):
    """Interpreta a solicitação e executa Calendar e Sheets."""

    if client is None:
        return [
            (
                "erro",
                "Configure GROQ_API_KEY no arquivo .env.",
            )
        ]

    if not PLANILHA_ID:
        return [
            (
                "erro",
                "PLANILHA_ID não encontrado no arquivo .env.",
            )
        ]

    if not mensagem_usuario or not mensagem_usuario.strip():
        return [
            (
                "erro",
                "Digite uma solicitação antes de continuar.",
            )
        ]

    # ETAPA 1: Interpretar os dados.
    try:
        dados = interpretar_solicitacao(
            mensagem_usuario
        )

    except Exception as erro:
        return [
            (
                "erro",
                f"Erro ao interpretar a solicitação: {erro}",
            )
        ]

    # ETAPA 2: Validar todos os campos.
    try:
        faltantes = validar_dados(dados)

        if faltantes:
            return [
                (
                    "texto",
                    "Não foi possível executar o processo. "
                    "Faltam informações: "
                    + ", ".join(faltantes)
                    + ". Informe os dados faltantes.",
                )
            ]

    except ValueError as erro:
        return [
            (
                "erro",
                str(erro),
            )
        ]

    # ETAPA 3: Verificar se a operação já foi realizada.
    try:
        controle = carregar_controle()

    except Exception as erro:
        return [
            ("erro", str(erro))
        ]

    chave = gerar_chave_operacao(dados)

    if chave not in controle:
        controle[chave] = {
            "nome": dados["nome"],
            "email": dados["email"],
            "data_hora_inicio": dados["data_hora_inicio"],
            "data_hora_fim": dados["data_hora_fim"],
            "calendar_concluido": False,
            "planilha_concluida": False,
        }

    operacao = controle[chave]

    resultados = []

    # ETAPA 4: Agendar no Google Calendar.
    if not operacao["calendar_concluido"]:

        try:
            resultado_calendar = agendar_entrevista_com_meet(
                nome_candidato=dados["nome"],
                email_candidato=dados["email"],
                data_hora_inicio=dados["data_hora_inicio"],
                data_hora_fim=dados["data_hora_fim"],
            )

            operacao["calendar_concluido"] = True
            operacao["resultado_calendar"] = resultado_calendar

            # Salvar imediatamente após o agendamento.
            salvar_controle(controle)

            resultados.append(
                ("calendar", resultado_calendar)
            )

        except Exception as erro:

            # Registrar a falha sem afirmar que houve agendamento.
            resultados.append(
                (
                    "erro_calendar",
                    "Falha no agendamento. Verifique o Google Calendar "
                    "antes de tentar novamente. Detalhes: "
                    + str(erro),
                )
            )

            return resultados

    else:
        resultados.append(
            (
                "calendar",
                "Entrevista já registrada como agendada anteriormente. "
                "Não foi criado outro evento.",
            )
        )

    # ETAPA 5: Registrar na planilha.
    if not operacao["planilha_concluida"]:

        try:
            resultado_planilha = registrar_candidato_planilha(
                nome=dados["nome"],
                email=dados["email"],
                vaga=dados["vaga"],
                status=dados["status"],
                data_hora_inicio=dados["data_hora_inicio"],
                data_hora_fim=dados["data_hora_fim"],
                id_planilha=PLANILHA_ID,
            )

            operacao["planilha_concluida"] = True
            operacao["resultado_planilha"] = resultado_planilha

            salvar_controle(controle)

            resultados.append(
                ("planilha", resultado_planilha)
            )

        except Exception as erro:

            resultados.append(
                (
                    "erro_planilha",
                    "A entrevista foi agendada, mas o registro "
                    "na planilha falhou. Não repita o agendamento. "
                    "Corrija a planilha e envie novamente a mesma "
                    "solicitação para tentar concluir somente "
                    "o registro. Detalhes: "
                    + str(erro),
                )
            )

            return resultados

    else:
        resultados.append(
            (
                "planilha",
                "Candidato já registrado anteriormente. "
                "Nenhuma linha duplicada foi criada.",
            )
        )

    # ETAPA 6: Confirmar conclusão.
    resultados.append(
        (
            "sucesso",
            "Processo concluído! Entrevista agendada e candidato "
            "registrado no Google Sheets.",
        )
    )

    return resultados
