import streamlit as st
import requests
import sqlite3
import json
import os
import io
import pandas as pd

from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo


# ============================================================
# CONFIGURAÇÃO DA PÁGINA
# ============================================================

st.set_page_config(
    page_title="Auditoria WhatsFlux",
    page_icon="🔎",
    layout="wide"
)


# ============================================================
# CONFIGURAÇÕES
# ============================================================

# Endpoint de login do WhatsFlux
API_LOGIN_URL = "https://api.whatsflux.com.br/auth/login"

# Endpoint dos tickets
API_TICKETS_URL = "https://api.whatsflux.com.br/tickets"

# Fila que será monitorada
QUEUE_ID = 18

# ------------------------------------------------------------
# ARQUIVO TEMPORÁRIO
# ------------------------------------------------------------
# Guarda somente o estado atual dos tickets.
#
# Exemplo:
#
# {
#     "10130": {
#         "ticket_id": 10130,
#         "cliente": "36202346487953",
#         "tecnico_id": 26,
#         "tecnico": "Thiago"
#     }
# }
#
# Ele serve para descobrir:
#
# Thiago -> Gabriel
#
TEMP_STATE_FILE = "estado_monitoramento.json"


# ------------------------------------------------------------
# BANCO PERMANENTE DE AUDITORIA
# ------------------------------------------------------------
#
# Somente transferências são gravadas aqui.
#
AUDIT_DB_FILE = "auditoria.db"


# Fuso horário utilizado pela empresa
TZ = ZoneInfo("America/Sao_Paulo")


# Intervalo padrão de consulta
DEFAULT_INTERVAL = 2.0


# ============================================================
# CSS
# ============================================================

st.markdown(
    """
    <style>

    .main-title {
        font-size: 30px;
        font-weight: 700;
        margin-bottom: 5px;
    }

    .subtitle {
        color: #94a3b8;
        margin-bottom: 20px;
    }

    .metric-card {
        background: #0f172a;
        border: 1px solid #334155;
        border-radius: 10px;
        padding: 18px;
        text-align: center;
    }

    .metric-number {
        font-size: 30px;
        font-weight: 700;
        color: #38bdf8;
    }

    .metric-label {
        color: #cbd5e1;
        font-size: 14px;
    }

    .transfer-card {
        background: #172033;
        border-left: 5px solid #f59e0b;
        padding: 12px 16px;
        border-radius: 8px;
        margin-bottom: 8px;
    }

    .status-online {
        color: #22c55e;
        font-weight: bold;
    }

    .status-error {
        color: #ef4444;
        font-weight: bold;
    }

    .small-info {
        color: #94a3b8;
        font-size: 13px;
    }

    </style>
    """,
    unsafe_allow_html=True
)


# ============================================================
# UTILITÁRIOS DE DATA/HORA
# ============================================================

def agora():
    """
    Retorna data/hora atual no fuso de São Paulo.
    """
    return datetime.now(TZ)


def agora_iso():
    """
    Retorna data/hora em formato ISO.
    """
    return agora().isoformat(timespec="seconds")


def formatar_data_hora(valor):
    """
    Converte uma data ISO para:
    DD/MM/YYYY HH:MM:SS
    """

    if not valor:
        return ""

    try:

        dt = datetime.fromisoformat(valor)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)

        return dt.astimezone(TZ).strftime(
            "%d/%m/%Y %H:%M:%S"
        )

    except Exception:

        return str(valor)


def data_inicio(d):
    """
    Retorna o início do dia.
    """

    return datetime(
        d.year,
        d.month,
        d.day,
        0,
        0,
        0,
        tzinfo=TZ
    )


def data_fim(d):
    """
    Retorna o final do dia.
    """

    return datetime(
        d.year,
        d.month,
        d.day,
        23,
        59,
        59,
        tzinfo=TZ
    )


# ============================================================
# ESTADO TEMPORÁRIO
# ============================================================

def carregar_estado_temporario():
    """
    Carrega o estado atual dos tickets.

    IMPORTANTE:
    Este arquivo NÃO é a auditoria.

    Ele existe somente para o sistema saber
    quem era o técnico anterior.
    """

    if not os.path.exists(TEMP_STATE_FILE):
        return {}

    try:

        with open(
            TEMP_STATE_FILE,
            "r",
            encoding="utf-8"
        ) as arquivo:

            dados = json.load(arquivo)

            if isinstance(dados, dict):
                return dados

    except Exception:
        pass

    return {}


def salvar_estado_temporario(estado):
    """
    Salva o estado temporário.

    Utiliza arquivo .tmp para reduzir o risco
    de corromper o JSON durante a gravação.
    """

    arquivo_temp = TEMP_STATE_FILE + ".tmp"

    with open(
        arquivo_temp,
        "w",
        encoding="utf-8"
    ) as arquivo:

        json.dump(
            estado,
            arquivo,
            indent=4,
            ensure_ascii=False
        )

    os.replace(
        arquivo_temp,
        TEMP_STATE_FILE
    )


# ============================================================
# BANCO DE AUDITORIA
# ============================================================

def conectar_banco():
    """
    Abre conexão com SQLite.
    """

    conn = sqlite3.connect(
        AUDIT_DB_FILE,
        timeout=30
    )

    conn.row_factory = sqlite3.Row

    return conn


def inicializar_banco():
    """
    Cria a tabela de auditoria caso ainda não exista.
    """

    conn = conectar_banco()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS auditoria (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            ticket_id INTEGER NOT NULL,

            ticket_uuid TEXT,

            contact_id INTEGER,

            cliente TEXT,

            telefone TEXT,

            tecnico_anterior_id INTEGER,

            tecnico_anterior TEXT,

            tecnico_atual_id INTEGER,

            tecnico_atual TEXT,

            evento TEXT NOT NULL,

            data_hora TEXT NOT NULL

        )
        """
    )

    # Índice para pesquisas por ticket
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_auditoria_ticket
        ON auditoria(ticket_id)
        """
    )

    # Índice para pesquisas por data
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_auditoria_data
        ON auditoria(data_hora)
        """
    )

    conn.commit()

    conn.close()


# ============================================================
# VERIFICAR SE UMA TRANSFERÊNCIA JÁ FOI GRAVADA
# ============================================================

def transferencia_ja_gravada(
    ticket_id,
    tecnico_anterior_id,
    tecnico_atual_id
):
    """
    Evita duplicação da mesma transferência.

    Exemplo:

    Thiago -> Gabriel

    Se o sistema identificar novamente a mesma combinação,
    não grava outra linha.
    """

    conn = conectar_banco()

    cursor = conn.execute(
        """
        SELECT id
        FROM auditoria
        WHERE ticket_id = ?
        AND tecnico_anterior_id = ?
        AND tecnico_atual_id = ?
        LIMIT 1
        """,
        (
            ticket_id,
            tecnico_anterior_id,
            tecnico_atual_id
        )
    )

    row = cursor.fetchone()

    conn.close()

    return row is not None


# ============================================================
# GRAVAR TRANSFERÊNCIA
# ============================================================

def gravar_transferencia(
    ticket,
    tecnico_anterior_id,
    tecnico_anterior,
    tecnico_atual_id,
    tecnico_atual
):
    """
    Grava uma transferência no banco permanente.
    """

    contato = ticket.get("contact") or {}

    ticket_id = ticket.get("id")

    ticket_uuid = ticket.get("uuid")

    contact_id = contato.get("id")

    cliente = (
        contato.get("name")
        or contato.get("number")
        or ""
    )

    telefone = contato.get("number") or ""


    # --------------------------------------------------------
    # Segurança contra duplicação
    # --------------------------------------------------------

    if transferencia_ja_gravada(
        ticket_id,
        tecnico_anterior_id,
        tecnico_atual_id
    ):

        return False


    # --------------------------------------------------------
    # Grava no banco
    # --------------------------------------------------------

    conn = conectar_banco()

    conn.execute(
        """
        INSERT INTO auditoria (

            ticket_id,
            ticket_uuid,

            contact_id,
            cliente,
            telefone,

            tecnico_anterior_id,
            tecnico_anterior,

            tecnico_atual_id,
            tecnico_atual,

            evento,
            data_hora

        )

        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            ticket_id,
            ticket_uuid,

            contact_id,
            cliente,
            telefone,

            tecnico_anterior_id,
            tecnico_anterior,

            tecnico_atual_id,
            tecnico_atual,

            "TRANSFERENCIA",

            agora_iso()
        )
    )

    conn.commit()

    conn.close()

    return True


# ============================================================
# LOGIN WHATSFLUX
# ============================================================

def criar_sessao_whatsflux():
    """
    Faz login na API do WhatsFlux.

    As credenciais são obtidas do Streamlit Secrets:

        WHATSFLUX_EMAIL
        WHATSFLUX_SENHA
    """

    try:

        email = st.secrets["WHATSFLUX_EMAIL"]

        senha = st.secrets["WHATSFLUX_SENHA"]

    except Exception:

        return (
            None,
            "Configure WHATSFLUX_EMAIL e "
            "WHATSFLUX_SENHA nos Secrets."
        )


    session = requests.Session()


    # Headers semelhantes aos utilizados
    # pelo navegador do WhatsFlux.

    session.headers.update({

        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),

        "Accept": (
            "application/json, text/plain, */*"
        ),

        "Content-Type": (
            "application/json;charset=UTF-8"
        ),

        "Referer": (
            "https://app.whatsflux.com.br/"
        ),

        "Origin": (
            "https://app.whatsflux.com.br"
        )
    })


    try:

        payload = {

            "email": email,

            "password": senha

        }


        response = session.post(

            API_LOGIN_URL,

            json=payload,

            timeout=15

        )


        if response.status_code not in [
            200,
            201,
            202
        ]:

            return (
                None,
                f"Falha no login. "
                f"HTTP {response.status_code}"
            )


        dados = response.json()


        token = (
            dados.get("token")
            or dados.get("access_token")
        )


        # ----------------------------------------------------
        # API usando Bearer Token
        # ----------------------------------------------------

        if token:

            session.headers.update({

                "Authorization":
                f"Bearer {token}"

            })


        else:

            # ------------------------------------------------
            # Caso a API utilize somente cookie
            # ------------------------------------------------

            if not session.cookies:

                return (
                    None,
                    "Login respondeu, mas não foi "
                    "encontrado token nem cookie."
                )


        return session, "OK"


    except requests.RequestException as e:

        return (
            None,
            f"Erro de conexão no login: {e}"
        )


    except Exception as e:

        return (
            None,
            f"Erro no login: {e}"
        )


# ============================================================
# BUSCAR TICKETS ABERTOS
# ============================================================

def buscar_tickets_abertos(session):
    """
    Consulta os tickets abertos da fila.

    Endpoint equivalente ao que você encontrou:

    /tickets
        ?pageNumber=1
        &status=open
        &showAll=true
        &queueIds=[18]
    """

    todos = []

    page_number = 1


    while True:

        params = {

            "pageNumber": page_number,

            "status": "open",

            "showAll": "true",

            "queueIds": f"[{QUEUE_ID}]"

        }


        try:

            response = session.get(

                API_TICKETS_URL,

                params=params,

                timeout=15

            )

        except requests.RequestException as e:

            raise RuntimeError(
                f"Erro ao consultar tickets: {e}"
            )


        # ----------------------------------------------------
        # Sessão expirada
        # ----------------------------------------------------

        if response.status_code == 401:

            raise RuntimeError(
                "Sessão expirada. HTTP 401."
            )


        if response.status_code != 200:

            raise RuntimeError(
                f"Erro na API de tickets. "
                f"HTTP {response.status_code}"
            )


        try:

            dados = response.json()

        except Exception:

            raise RuntimeError(
                "A API de tickets não retornou JSON válido."
            )


        tickets = dados.get("tickets") or []

        todos.extend(tickets)


        has_more = dados.get(
            "hasMore",
            False
        )


        if not has_more:

            break


        page_number += 1


        # Segurança
        if page_number > 100:

            break


    return todos


# ============================================================
# PROCESSAR UM TICKET
# ============================================================

def processar_ticket(
    ticket,
    estado
):
    """
    Processa um ticket individual.

    REGRAS:

    1. Sem técnico -> ignora.

    2. Primeira vez com técnico:
       guarda no temporário.

    3. Mesmo técnico:
       não grava auditoria.

    4. Técnico mudou:
       grava transferência.

    5. Atualiza o temporário para o novo técnico.
    """

    ticket_id = ticket.get("id")


    if not ticket_id:

        return None


    user = ticket.get("user")

    user_id = ticket.get("userId")


    # ========================================================
    # SEM TÉCNICO
    # ========================================================

    if not user or not user_id:

        return None


    tecnico_atual = user.get("name")


    if not tecnico_atual:

        return None


    contato = ticket.get("contact") or {}


    cliente = (
        contato.get("name")
        or contato.get("number")
        or ""
    )


    # ========================================================
    # ESTADO ATUAL
    # ========================================================

    dados_atuais = {

        "ticket_id": ticket_id,

        "ticket_uuid":
        ticket.get("uuid"),

        "contact_id":
        contato.get("id"),

        "cliente":
        cliente,

        "telefone":
        contato.get("number") or "",

        "tecnico_id":
        user_id,

        "tecnico":
        tecnico_atual,

        "detectado_em":
        agora_iso()
    }


    chave = str(ticket_id)


    # ========================================================
    # PRIMEIRA VEZ
    # ========================================================

    if chave not in estado:

        estado[chave] = dados_atuais

        return {

            "tipo":
            "ENTRADA_MONITORAMENTO",

            "ticket_id":
            ticket_id,

            "tecnico":
            tecnico_atual,

            "cliente":
            cliente
        }


    # ========================================================
    # DADOS ANTERIORES
    # ========================================================

    anterior = estado[chave]


    tecnico_anterior_id = (
        anterior.get("tecnico_id")
    )


    tecnico_anterior = (
        anterior.get("tecnico")
    )


    # ========================================================
    # MESMO TÉCNICO
    # ========================================================

    if (
        str(tecnico_anterior_id)
        ==
        str(user_id)
    ):

        # Atualizamos informações auxiliares,
        # mas NÃO gravamos auditoria.

        estado[chave] = {

            **anterior,

            "ticket_uuid":
            ticket.get("uuid"),

            "contact_id":
            contato.get("id"),

            "cliente":
            cliente,

            "telefone":
            contato.get("number") or "",

            "tecnico_id":
            user_id,

            "tecnico":
            tecnico_atual

        }

        return None


    # ========================================================
    # TÉCNICO MUDOU
    # ========================================================

    gravado = gravar_transferencia(

        ticket=ticket,

        tecnico_anterior_id=
        tecnico_anterior_id,

        tecnico_anterior=
        tecnico_anterior,

        tecnico_atual_id=
        user_id,

        tecnico_atual=
        tecnico_atual
    )


    # Atualiza o estado atual.
    estado[chave] = dados_atuais


    if not gravado:

        return None


    return {

        "tipo":
        "TRANSFERENCIA",

        "ticket_id":
        ticket_id,

        "cliente":
        cliente,

        "anterior":
        tecnico_anterior,

        "atual":
        tecnico_atual

    }


# ============================================================
# EXECUTAR UM CICLO
# ============================================================

def executar_monitoramento(session):
    """
    Executa uma consulta completa.

    O processo é:

        API
          ↓
        tickets abertos
          ↓
        verificar técnico
          ↓
        comparar com estado temporário
          ↓
        se mudou:
            gravar auditoria
    """

    estado = carregar_estado_temporario()


    tickets = buscar_tickets_abertos(
        session
    )


    transferencias = []

    entradas = 0


    # Guarda os IDs atualmente abertos.
    ids_abertos = set()


    for ticket in tickets:

        ticket_id = ticket.get("id")


        if ticket_id:

            ids_abertos.add(
                str(ticket_id)
            )


        resultado = processar_ticket(

            ticket,

            estado

        )


        if not resultado:

            continue


        if (
            resultado["tipo"]
            ==
            "ENTRADA_MONITORAMENTO"
        ):

            entradas += 1


        elif (
            resultado["tipo"]
            ==
            "TRANSFERENCIA"
        ):

            transferencias.append(
                resultado
            )


    # ========================================================
    # LIMPEZA AUTOMÁTICA DO ESTADO
    # ========================================================
    #
    # Se um ticket não está mais aberto,
    # ele deixa de ser necessário no estado temporário.
    #
    # Isso evita que o JSON cresça indefinidamente.
    #
    estado_limpo = {}

    for chave, item in estado.items():

        if str(chave) in ids_abertos:

            estado_limpo[chave] = item


    estado = estado_limpo


    # Salva o estado atualizado
    salvar_estado_temporario(
        estado
    )


    return {

        "tickets_abertos":
        len(tickets),

        "ids_abertos":
        ids_abertos,

        "estado":
        estado,

        "entradas":
        entradas,

        "transferencias":
        transferencias

    }


# ============================================================
# CONSULTAR AUDITORIA
# ============================================================

def consultar_auditoria(
    data_inicial,
    data_final,
    tecnico=None
):
    """
    Consulta o banco permanente de auditoria.
    """

    inicio = (
        data_inicio(
            data_inicial
        ).isoformat()
    )


    fim = (
        data_fim(
            data_final
        ).isoformat()
    )


    conn = conectar_banco()


    sql = """
        SELECT

            id,

            ticket_id,

            ticket_uuid,

            contact_id,

            cliente,

            telefone,

            tecnico_anterior_id,

            tecnico_anterior,

            tecnico_atual_id,

            tecnico_atual,

            evento,

            data_hora

        FROM auditoria

        WHERE data_hora >= ?

        AND data_hora <= ?
    """


    params = [

        inicio,

        fim

    ]


    # --------------------------------------------------------
    # Filtro por técnico
    # --------------------------------------------------------

    if (
        tecnico
        and
        tecnico != "Todos"
    ):

        sql += """
            AND (
                tecnico_anterior = ?
                OR tecnico_atual = ?
            )
        """

        params.extend([

            tecnico,

            tecnico

        ])


    sql += """
        ORDER BY data_hora DESC
    """


    df = pd.read_sql_query(

        sql,

        conn,

        params=params

    )


    conn.close()


    return df


# ============================================================
# LISTAR TÉCNICOS DA AUDITORIA
# ============================================================

def listar_tecnicos_auditoria():
    """
    Retorna todos os técnicos que aparecem
    no banco de auditoria.
    """

    conn = conectar_banco()


    df = pd.read_sql_query(
        """
        SELECT tecnico_anterior AS tecnico

        FROM auditoria

        WHERE tecnico_anterior IS NOT NULL

        UNION

        SELECT tecnico_atual AS tecnico

        FROM auditoria

        WHERE tecnico_atual IS NOT NULL

        ORDER BY tecnico
        """,
        conn
    )


    conn.close()


    if df.empty:

        return []


    return (
        df["tecnico"]
        .dropna()
        .tolist()
    )


# ============================================================
# QUANTIDADE DE AUDITORIAS
# ============================================================

def quantidade_auditoria():
    """
    Retorna quantidade total de transferências registradas.
    """

    conn = conectar_banco()


    cursor = conn.execute(
        """
        SELECT COUNT(*)
        FROM auditoria
        """
    )


    quantidade = cursor.fetchone()[0]


    conn.close()


    return quantidade


# ============================================================
# DATA DO ESTADO TEMPORÁRIO
# ============================================================

def data_estado(item):
    """
    Retorna a data de detecção do registro temporário.
    """

    valor = item.get(
        "detectado_em"
    )


    try:

        dt = datetime.fromisoformat(
            valor
        )


        if dt.tzinfo is None:

            dt = dt.replace(
                tzinfo=TZ
            )


        return dt


    except Exception:

        return None


# ============================================================
# LIMPAR ESTADO TEMPORÁRIO
# ============================================================

def limpar_estado_temporario(
    data_inicial,
    data_final,
    ids_abertos
):
    """
    Remove registros antigos do estado temporário.

    IMPORTANTE:

    Tickets que ainda estão abertos NÃO são removidos.

    Isso evita perder a referência necessária para detectar
    uma futura transferência.
    """

    estado = carregar_estado_temporario()


    inicio = data_inicio(
        data_inicial
    )


    fim = data_fim(
        data_final
    )


    candidatos = []

    preservados = []


    for chave, item in estado.items():

        dt = data_estado(item)


        if not dt:

            continue


        if not (
            inicio
            <=
            dt
            <=
            fim
        ):

            continue


        # ----------------------------------------------------
        # Ticket ainda aberto
        # ----------------------------------------------------

        if str(chave) in ids_abertos:

            preservados.append(
                item
            )


        else:

            candidatos.append(
                chave
            )


    # --------------------------------------------------------
    # Remove os antigos
    # --------------------------------------------------------

    for chave in candidatos:

        estado.pop(
            chave,
            None
        )


    salvar_estado_temporario(
        estado
    )


    return {

        "removidos":
        len(candidatos),

        "preservados":
        len(preservados)

    }


# ============================================================
# CONTAGEM DO ESTADO TEMPORÁRIO
# ============================================================

def quantidade_estado_temporario():

    estado = carregar_estado_temporario()

    return len(estado)


# ============================================================
# GERAR CSV
# ============================================================

def gerar_csv(df):
    """
    Converte o DataFrame da auditoria para CSV.

    UTF-8-SIG:
    facilita abertura diretamente no Excel.

    Separador:
    ponto e vírgula.
    """

    if df.empty:

        return b""


    buffer = io.StringIO()


    df.to_csv(

        buffer,

        index=False,

        sep=";",

        encoding="utf-8-sig"

    )


    return buffer.getvalue().encode(
        "utf-8-sig"
    )


# ============================================================
# INICIALIZAÇÃO DO BANCO
# ============================================================

inicializar_banco()


# ============================================================
# SESSION STATE
# ============================================================

if (
    "whats_session"
    not in
    st.session_state
):

    st.session_state.whats_session = None


if (
    "login_status"
    not in
    st.session_state
):

    st.session_state.login_status = ""


if (
    "monitorando"
    not in
    st.session_state
):

    st.session_state.monitorando = True


if (
    "ultima_execucao"
    not in
    st.session_state
):

    st.session_state.ultima_execucao = None


if (
    "ultima_transferencias"
    not in
    st.session_state
):

    st.session_state.ultima_transferencias = []


if (
    "total_ciclos"
    not in
    st.session_state
):

    st.session_state.total_ciclos = 0


# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.title(
    "⚙️ Configurações"
)


# ------------------------------------------------------------
# Intervalo
# ------------------------------------------------------------

intervalo = st.sidebar.number_input(

    "Consultar API a cada (segundos)",

    min_value=0.5,

    max_value=300.0,

    value=DEFAULT_INTERVAL,

    step=0.5,

    key="intervalo_monitor"

)


st.sidebar.caption(
    "Ex.: 2 = consulta a cada 2 segundos."
)


st.sidebar.divider()


# ------------------------------------------------------------
# Controle do monitor
# ------------------------------------------------------------

if st.sidebar.button(
    "▶️ Iniciar monitoramento",
    disabled=st.session_state.monitorando,
    use_container_width=True
):

    st.session_state.monitorando = True

    st.rerun()


if st.sidebar.button(
    "⏸️ Pausar monitoramento",
    disabled=not st.session_state.monitorando,
    use_container_width=True
):

    st.session_state.monitorando = False

    st.rerun()


st.sidebar.divider()


st.sidebar.write(
    f"**Fila monitorada:** {QUEUE_ID}"
)


st.sidebar.write(
    f"**Intervalo:** {intervalo:.1f}s"
)


if st.session_state.monitorando:

    st.sidebar.success(
        "🟢 Monitoramento ativo"
    )

else:

    st.sidebar.warning(
        "⏸️ Monitoramento pausado"
    )


# ============================================================
# LIMPEZA DO ESTADO TEMPORÁRIO
# ============================================================

st.sidebar.divider()

st.sidebar.subheader(
    "🧹 Estado temporário"
)


quantidade_temp = (
    quantidade_estado_temporario()
)


st.sidebar.write(
    f"Registros atuais: **{quantidade_temp}**"
)


st.sidebar.caption(
    "O estado temporário serve somente para "
    "identificar mudanças de técnico."
)


data_limpeza_inicio = st.sidebar.date_input(

    "Limpar a partir de",

    value=(
        date.today()
        -
        timedelta(days=30)
    ),

    key="data_limpeza_inicio"

)


data_limpeza_fim = st.sidebar.date_input(

    "Limpar até",

    value=date.today(),

    key="data_limpeza_fim"

)


if st.sidebar.button(
    "🧹 Limpar estado temporário",
    use_container_width=True
):

    # --------------------------------------------------------
    # Busca novamente os tickets abertos
    # para proteger os que ainda estão em atendimento.
    # --------------------------------------------------------

    try:

        tickets_abertos = buscar_tickets_abertos(

            st.session_state.whats_session

        )


        ids_abertos = {

            str(ticket.get("id"))

            for ticket in tickets_abertos

            if ticket.get("id")

        }


        resultado_limpeza = (
            limpar_estado_temporario(

                data_inicial=
                data_limpeza_inicio,

                data_final=
                data_limpeza_fim,

                ids_abertos=
                ids_abertos

            )
        )


        st.sidebar.success(

            f"✅ {resultado_limpeza['removidos']} "
            f"registro(s) removido(s)."

        )


        if (
            resultado_limpeza["preservados"]
            > 0
        ):

            st.sidebar.info(

                f"🔒 "
                f"{resultado_limpeza['preservados']} "
                f"atendimento(s) ainda aberto(s) "
                f"foram preservados."

            )


    except Exception as e:

        st.sidebar.error(
            f"Erro na limpeza: {e}"
        )


# ============================================================
# TÍTULO
# ============================================================

st.markdown(

    '<div class="main-title">'
    '🔎 Auditoria de Transferências WhatsFlux'
    '</div>',

    unsafe_allow_html=True

)


st.markdown(

    '<div class="subtitle">'
    'Monitora responsáveis e registra somente '
    'alterações de técnico.'
    '</div>',

    unsafe_allow_html=True

)


# ============================================================
# LOGIN
# ============================================================

if (
    st.session_state.whats_session
    is None
):

    session, mensagem = (
        criar_sessao_whatsflux()
    )


    if session:

        st.session_state.whats_session = (
            session
        )

        st.session_state.login_status = (
            "OK"
        )

    else:

        st.session_state.login_status = (
            mensagem
        )


# ------------------------------------------------------------
# Se não conseguiu login
# ------------------------------------------------------------

if (
    st.session_state.whats_session
    is None
):

    st.error(

        f"❌ "
        f"{st.session_state.login_status}"

    )

    st.stop()


# ============================================================
# FRAGMENTO DE MONITORAMENTO
# ============================================================

run_every = (

    intervalo

    if st.session_state.monitorando

    else None

)


@st.fragment(
    run_every=run_every,
    key="monitor_whatsflux"
)
def painel_monitoramento():

    # ========================================================
    # EXECUTAR MONITORAMENTO
    # ========================================================

    try:

        resultado = (
            executar_monitoramento(

                st.session_state.whats_session

            )
        )


        st.session_state.total_ciclos += 1


        st.session_state.ultima_execucao = (
            agora()
        )


        st.session_state.ultima_transferencias = (
            resultado.get(
                "transferencias",
                []
            )
        )


    except RuntimeError as e:

        # ----------------------------------------------------
        # Se a sessão expirou, tenta fazer login novamente.
        # ----------------------------------------------------

        if "401" in str(e):

            nova_session, mensagem = (
                criar_sessao_whatsflux()
            )


            if nova_session:

                st.session_state.whats_session = (
                    nova_session
                )

                st.warning(
                    "🔄 Sessão renovada. "
                    "O monitoramento continuará."
                )

            else:

                st.error(
                    f"❌ Não foi possível renovar "
                    f"a sessão: {mensagem}"
                )


            return


        st.error(
            f"❌ {e}"
        )

        return


    except Exception as e:

        st.error(
            f"❌ Erro no monitoramento: {e}"
        )

        return


    # ========================================================
    # DADOS DO CICLO
    # ========================================================

    estado = resultado.get(
        "estado",
        {}
    )


    tickets_abertos = resultado.get(
        "tickets_abertos",
        0
    )


    entradas = resultado.get(
        "entradas",
        0
    )


    transferencias = resultado.get(
        "transferencias",
        []
    )


    # ========================================================
    # INDICADORES
    # ========================================================

    col1, col2, col3, col4 = (
        st.columns(4)
    )


    with col1:

        st.markdown(

            f"""
            <div class="metric-card">

                <div class="metric-number">
                    {tickets_abertos}
                </div>

                <div class="metric-label">
                    Tickets abertos
                </div>

            </div>
            """,

            unsafe_allow_html=True

        )


    with col2:

        st.markdown(

            f"""
            <div class="metric-card">

                <div class="metric-number">
                    {len(estado)}
                </div>

                <div class="metric-label">
                    Em monitoramento
                </div>

            </div>
            """,

            unsafe_allow_html=True

        )


    with col3:

        st.markdown(

            f"""
            <div class="metric-card">

                <div class="metric-number">
                    {entradas}
                </div>

                <div class="metric-label">
                    Novos responsáveis
                </div>

            </div>
            """,

            unsafe_allow_html=True

        )


    with col4:

        st.markdown(

            f"""
            <div class="metric-card">

                <div class="metric-number">
                    {quantidade_auditoria()}
                </div>

                <div class="metric-label">
                    Transferências auditadas
                </div>

            </div>
            """,

            unsafe_allow_html=True

        )


    st.write("")


    # ========================================================
    # STATUS
    # ========================================================

    ultima = (
        st.session_state.ultima_execucao
    )


    if ultima:

        horario = ultima.strftime(
            "%d/%m/%Y %H:%M:%S"
        )

    else:

        horario = "-"


    st.markdown(

        f"""
        <div class="small-info">

            🟢 Monitoramento ativo |

            Última consulta:
            <strong>{horario}</strong>

            |

            Ciclos executados:
            <strong>
                {st.session_state.total_ciclos}
            </strong>

        </div>
        """,

        unsafe_allow_html=True

    )


    # ========================================================
    # TRANSFERÊNCIAS DETECTADAS
    # ========================================================

    st.write("")


    st.subheader(
        "🔄 Transferências detectadas neste ciclo"
    )


    if transferencias:

        for item in transferencias:

            st.markdown(

                f"""
                <div class="transfer-card">

                    <strong>
                        Ticket #{item["ticket_id"]}
                    </strong>

                    &nbsp; | &nbsp;

                    Cliente:

                    <strong>
                        {item["cliente"]}
                    </strong>

                    <br><br>

                    👤

                    <strong>
                        {item["anterior"]}
                    </strong>

                    &nbsp; ➜ &nbsp;

                    <strong>
                        {item["atual"]}
                    </strong>

                </div>
                """,

                unsafe_allow_html=True

            )

    else:

        st.info(
            "Nenhuma transferência detectada neste ciclo."
        )


    # ========================================================
    # ATENDIMENTOS ATUALMENTE MONITORADOS
    # ========================================================

    st.write("")

    with st.expander(
        "👥 Atendimentos atualmente monitorados",
        expanded=False
    ):

        if estado:

            linhas = []


            for item in estado.values():

                linhas.append({

                    "Ticket":
                    item.get("ticket_id"),

                    "Cliente":
                    item.get("cliente"),

                    "Telefone":
                    item.get("telefone"),

                    "Técnico":
                    item.get("tecnico"),

                    "Detectado em":
                    formatar_data_hora(
                        item.get(
                            "detectado_em"
                        )
                    )

                })


            df_estado = pd.DataFrame(
                linhas
            )


            if not df_estado.empty:

                st.dataframe(

                    df_estado,

                    use_container_width=True,

                    hide_index=True

                )

        else:

            st.info(
                "Nenhum atendimento com técnico "
                "está sendo monitorado."
            )


    # ========================================================
    # HISTÓRICO DE TRANSFERÊNCIAS
    # ========================================================
    #
    # Fica DEPOIS dos atendimentos atualmente monitorados,
    # conforme você pediu.
    # ========================================================

    st.divider()


    st.header(
        "📊 Histórico de Transferências"
    )


    col1, col2, col3 = (
        st.columns(3)
    )


    with col1:

        data_inicio_auditoria = (
            st.date_input(

                "Data inicial",

                value=date.today(),

                key="auditoria_data_inicio"

            )
        )


    with col2:

        data_fim_auditoria = (
            st.date_input(

                "Data final",

                value=date.today(),

                key="auditoria_data_fim"

            )
        )


    with col3:

        tecnicos = (

            ["Todos"]

            +
            listar_tecnicos_auditoria()

        )


        tecnico_filtro = (
            st.selectbox(

                "Técnico",

                tecnicos,

                key="auditoria_tecnico"

            )
        )


    # ========================================================
    # CONSULTAR BANCO
    # ========================================================

    df_auditoria = consultar_auditoria(

        data_inicial=
        data_inicio_auditoria,

        data_final=
        data_fim_auditoria,

        tecnico=
        tecnico_filtro

    )


    # ========================================================
    # MOSTRAR RESULTADOS
    # ========================================================

    if df_auditoria.empty:

        st.info(

            "Nenhuma transferência encontrada "
            "no período selecionado."

        )

    else:

        st.success(

            f"{len(df_auditoria)} "
            f"transferência(s) encontrada(s)."

        )


        # ----------------------------------------------------
        # DataFrame para exibição
        # ----------------------------------------------------

        df_exibicao = (
            df_auditoria.copy()
        )


        if "data_hora" in (
            df_exibicao.columns
        ):

            df_exibicao["data_hora"] = (

                pd.to_datetime(

                    df_exibicao[
                        "data_hora"
                    ],

                    errors="coerce"

                )

                .dt.strftime(
                    "%d/%m/%Y %H:%M:%S"
                )

            )


        # ----------------------------------------------------
        # Tabela
        # ----------------------------------------------------

        st.dataframe(

            df_exibicao,

            use_container_width=True,

            hide_index=True

        )


        # ====================================================
        # DOWNLOAD CSV
        # ====================================================

        csv = gerar_csv(
            df_auditoria
        )


        nome_arquivo = (

            "auditoria_transferencias_"

            +
            data_inicio_auditoria.strftime(
                "%Y%m%d"
            )

            +
            "_"

            +
            data_fim_auditoria.strftime(
                "%Y%m%d"
            )

            +
            ".csv"

        )


        st.download_button(

            label=
            "⬇️ Baixar auditoria em CSV",

            data=csv,

            file_name=
            nome_arquivo,

            mime=
            "text/csv",

            key=
            "download_csv_auditoria"

        )


# ============================================================
# EXECUTAR PAINEL
# ============================================================

painel_monitoramento()
