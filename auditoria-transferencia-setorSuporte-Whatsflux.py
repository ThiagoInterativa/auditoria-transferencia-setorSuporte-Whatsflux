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

API_LOGIN_URL = "https://api.whatsflux.com.br/auth/login"
API_TICKETS_URL = "https://api.whatsflux.com.br/tickets"

# Fila que será monitorada
QUEUE_ID = 18

# Arquivos locais
TEMP_STATE_FILE = "estado_monitoramento.json"
AUDIT_DB_FILE = "auditoria.db"

# Fuso horário
TZ = ZoneInfo("America/Sao_Paulo")

# Intervalo padrão
DEFAULT_INTERVAL = 2.0


# ============================================================
# CSS
# ============================================================
#
# IMPORTANTE:
# Não usamos HTML para montar os cards.
# O CSS abaixo apenas personaliza os componentes nativos
# do Streamlit.
# ============================================================

st.markdown(
    """
    <style>

    /* ======================================================
       TÍTULO
       ====================================================== */

    .main-title-streamlit {
        font-size: 30px;
        font-weight: 700;
        margin-bottom: 4px;
    }

    .subtitle-streamlit {
        color: #64748b;
        margin-bottom: 20px;
        font-size: 15px;
    }


    /* ======================================================
       CARDS SUPERIORES
       ====================================================== */

    div[data-testid="stMetric"] {

        background-color: #0f172a;

        border: 1px solid #334155;

        border-radius: 10px;

        padding: 16px 18px;

        min-height: 105px;

        box-shadow: none;

    }


    div[data-testid="stMetric"] label {

        color: #cbd5e1 !important;

        font-size: 14px !important;

        font-weight: 500 !important;

    }


    div[data-testid="stMetricValue"] {

        color: #38bdf8 !important;

        font-size: 30px !important;

        font-weight: 700 !important;

    }


    /* ======================================================
       ESPAÇAMENTO DOS CARDS
       ====================================================== */

    div[data-testid="stHorizontalBlock"] {
        gap: 1rem;
    }


    /* ======================================================
       EXPANDER
       ====================================================== */

    div[data-testid="stExpander"] {

        border: 1px solid #334155;

        border-radius: 10px;

    }


    /* ======================================================
       BOTÕES
       ====================================================== */

    .stButton > button {

        border-radius: 8px;

    }


    /* ======================================================
       TABELAS
       ====================================================== */

    div[data-testid="stDataFrame"] {

        border-radius: 8px;

    }

    </style>
    """,
    unsafe_allow_html=True
)


# ============================================================
# UTILITÁRIOS DE DATA/HORA
# ============================================================

def agora():
    return datetime.now(TZ)


def agora_iso():
    return agora().isoformat(timespec="seconds")


def formatar_data_hora(valor):

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

    conn = sqlite3.connect(
        AUDIT_DB_FILE,
        timeout=30
    )

    conn.row_factory = sqlite3.Row

    return conn


def inicializar_banco():

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

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_auditoria_ticket
        ON auditoria(ticket_id)
        """
    )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_auditoria_data
        ON auditoria(data_hora)
        """
    )

    conn.commit()

    conn.close()


# ============================================================
# CONSULTAR ÚLTIMO EVENTO DO TICKET
# ============================================================

def buscar_ultimo_evento(ticket_id):

    conn = conectar_banco()

    cursor = conn.execute(
        """
        SELECT *
        FROM auditoria
        WHERE ticket_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (ticket_id,)
    )

    row = cursor.fetchone()

    conn.close()

    if row:
        return dict(row)

    return None


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

        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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


# ============================================================
# LOGIN WHATSFLUX
# ============================================================

def criar_sessao_whatsflux():

    try:

        email = st.secrets["WHATSFLUX_EMAIL"]

        senha = st.secrets["WHATSFLUX_SENHA"]

    except Exception:

        return (
            None,
            "Configure WHATSFLUX_EMAIL e WHATSFLUX_SENHA nos Secrets."
        )


    session = requests.Session()


    session.headers.update(
        {

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
        }
    )


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


        if response.status_code not in [200, 201, 202]:

            return (
                None,
                f"Falha no login. HTTP {response.status_code}"
            )


        dados = response.json()


        token = (
            dados.get("token")
            or dados.get("access_token")
        )


        if token:

            session.headers.update(
                {
                    "Authorization": f"Bearer {token}"
                }
            )

        else:

            if not session.cookies:

                return (
                    None,
                    "Login respondeu, mas não foi encontrado token nem cookie."
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

    if session is None:

        raise RuntimeError(
            "Sessão do WhatsFlux não está disponível."
        )


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


        if response.status_code == 401:

            raise RuntimeError(
                "Sessão expirada ou não autorizada (HTTP 401)."
            )


        if response.status_code != 200:

            raise RuntimeError(
                f"Erro na API de tickets. HTTP {response.status_code}"
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


        if page_number > 100:

            break


    return todos


# ============================================================
# PROCESSAMENTO DO TICKET
# ============================================================

def processar_ticket(
    ticket,
    estado
):

    ticket_id = ticket.get("id")


    if not ticket_id:

        return None


    user = ticket.get("user")

    user_id = ticket.get("userId")


    # ========================================================
    # ATENDIMENTO SEM TÉCNICO
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
    # DADOS ATUAIS
    # ========================================================

    dados_atuais = {

        "ticket_id": ticket_id,

        "ticket_uuid": ticket.get("uuid"),

        "contact_id": contato.get("id"),

        "cliente": cliente,

        "telefone": contato.get("number") or "",

        "tecnico_id": user_id,

        "tecnico": tecnico_atual,

        "detectado_em": agora_iso()
    }


    chave = str(ticket_id)


    # ========================================================
    # PRIMEIRA VEZ
    # ========================================================

    if chave not in estado:

        estado[chave] = dados_atuais

        return {

            "tipo": "ENTRADA_MONITORAMENTO",

            "ticket_id": ticket_id,

            "tecnico": tecnico_atual,

            "cliente": cliente
        }


    anterior = estado[chave]


    tecnico_anterior_id = anterior.get(
        "tecnico_id"
    )

    tecnico_anterior = anterior.get(
        "tecnico"
    )


    # ========================================================
    # MESMO TÉCNICO
    # ========================================================

    if str(tecnico_anterior_id) == str(user_id):

        estado[chave] = {

            **anterior,

            "ticket_uuid": ticket.get("uuid"),

            "contact_id": contato.get("id"),

            "cliente": cliente,

            "telefone": contato.get("number") or "",

            "tecnico_id": user_id,

            "tecnico": tecnico_atual
        }

        return None


    # ========================================================
    # TÉCNICO MUDOU
    # ========================================================

    gravar_transferencia(

        ticket=ticket,

        tecnico_anterior_id=tecnico_anterior_id,

        tecnico_anterior=tecnico_anterior,

        tecnico_atual_id=user_id,

        tecnico_atual=tecnico_atual
    )


    # Atualiza o estado para a nova situação.

    estado[chave] = dados_atuais


    return {

        "tipo": "TRANSFERENCIA",

        "ticket_id": ticket_id,

        "cliente": cliente,

        "anterior": tecnico_anterior,

        "atual": tecnico_atual
    }


# ============================================================
# EXECUTAR UM CICLO DE MONITORAMENTO
# ============================================================

def executar_monitoramento(session):

    estado = carregar_estado_temporario()


    # --------------------------------------------------------
    # Busca todos os tickets atualmente abertos
    # --------------------------------------------------------

    tickets = buscar_tickets_abertos(
        session
    )


    transferencias = []

    entradas = 0


    # --------------------------------------------------------
    # IDs atualmente abertos
    # --------------------------------------------------------

    ids_abertos = set()


    for ticket in tickets:

        ticket_id = ticket.get("id")

        if ticket_id:

            ids_abertos.add(
                str(ticket_id)
            )


    # --------------------------------------------------------
    # PROCESSA OS TICKETS ABERTOS
    # --------------------------------------------------------

    for ticket in tickets:

        resultado = processar_ticket(
            ticket,
            estado
        )


        if not resultado:

            continue


        if resultado["tipo"] == "ENTRADA_MONITORAMENTO":

            entradas += 1


        elif resultado["tipo"] == "TRANSFERENCIA":

            transferencias.append(
                resultado
            )


    # --------------------------------------------------------
    # REMOVE TICKETS FECHADOS DO ESTADO TEMPORÁRIO
    # --------------------------------------------------------

    chaves_para_remover = []


    for chave_ticket in list(estado.keys()):

        if chave_ticket not in ids_abertos:

            chaves_para_remover.append(
                chave_ticket
            )


    for chave_ticket in chaves_para_remover:

        estado.pop(
            chave_ticket,
            None
        )


    # --------------------------------------------------------
    # SALVA O ESTADO
    # --------------------------------------------------------

    salvar_estado_temporario(
        estado
    )


    return {

        "tickets_abertos": len(tickets),

        "ids_abertos": ids_abertos,

        "estado": estado,

        "entradas": entradas,

        "transferencias": transferencias
    }


# ============================================================
# AUDITORIA - CONSULTA
# ============================================================

def consultar_auditoria(
    data_inicial,
    data_final,
    tecnico=None
):

    inicio = data_inicio(
        data_inicial
    ).isoformat()


    fim = data_fim(
        data_final
    ).isoformat()


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


    if tecnico and tecnico != "Todos":

        sql += """
            AND (
                tecnico_anterior = ?
                OR tecnico_atual = ?
            )
        """

        params.extend(
            [
                tecnico,
                tecnico
            ]
        )


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
# TÉCNICOS EXISTENTES NA AUDITORIA
# ============================================================

def listar_tecnicos_auditoria():

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
# ESTATÍSTICAS
# ============================================================

def quantidade_auditoria():

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
# ESTADO TEMPORÁRIO - DATA
# ============================================================

def data_estado(item):

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
# VERIFICAR ESTADOS ABERTOS NO PERÍODO
# ============================================================

def encontrar_estados_abertos_no_periodo(
    estado,
    ids_abertos,
    data_inicial,
    data_final
):

    inicio = data_inicio(
        data_inicial
    )

    fim = data_fim(
        data_final
    )


    encontrados = []


    for chave, item in estado.items():

        dt = data_estado(item)


        if not dt:

            continue


        dentro_periodo = (
            inicio <= dt <= fim
        )


        if not dentro_periodo:

            continue


        if str(chave) in ids_abertos:

            encontrados.append(
                item
            )


    return encontrados


# ============================================================
# LIMPAR ESTADO TEMPORÁRIO
# ============================================================

def limpar_estado_temporario(
    data_inicial,
    data_final,
    ids_abertos,
    confirmar=False
):

    estado = carregar_estado_temporario()


    inicio = data_inicio(
        data_inicial
    )

    fim = data_fim(
        data_final
    )


    candidatos = []

    abertos_no_periodo = []


    for chave, item in estado.items():

        dt = data_estado(item)


        if not dt:

            continue


        if not (
            inicio <= dt <= fim
        ):

            continue


        if str(chave) in ids_abertos:

            abertos_no_periodo.append(
                item
            )

        else:

            candidatos.append(
                chave
            )


    # --------------------------------------------------------
    # Segurança
    # --------------------------------------------------------

    if abertos_no_periodo and not confirmar:

        return {

            "status": "CONFIRMACAO_NECESSARIA",

            "removiveis": len(
                candidatos
            ),

            "abertos": abertos_no_periodo
        }


    # --------------------------------------------------------
    # Exclusão
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

        "status": "OK",

        "removidos": len(
            candidatos
        ),

        "abertos": len(
            abertos_no_periodo
        )
    }


# ============================================================
# EXCLUIR AUDITORIA
# ============================================================

def excluir_auditoria_periodo(
    data_inicial,
    data_final
):

    inicio = data_inicio(
        data_inicial
    ).isoformat()


    fim = data_fim(
        data_final
    ).isoformat()


    conn = conectar_banco()


    cursor = conn.execute(
        """
        DELETE FROM auditoria

        WHERE data_hora >= ?

        AND data_hora <= ?
        """,
        (
            inicio,
            fim
        )
    )


    removidos = cursor.rowcount


    conn.commit()

    conn.close()


    return removidos


# ============================================================
# GERAR CSV
# ============================================================

def gerar_csv(df):

    colunas = [

        "id",
        "ticket_id",
        "ticket_uuid",
        "contact_id",
        "cliente",
        "telefone",

        "tecnico_anterior_id",
        "tecnico_anterior",

        "tecnico_atual_id",
        "tecnico_atual",

        "evento",
        "data_hora"
    ]


    if df is None or df.empty:

        df = pd.DataFrame(
            columns=colunas
        )

    else:

        for coluna in colunas:

            if coluna not in df.columns:

                df[coluna] = ""


        df = df[colunas]


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
# INICIALIZAÇÃO
# ============================================================

inicializar_banco()


if "whats_session" not in st.session_state:

    st.session_state.whats_session = None


if "login_status" not in st.session_state:

    st.session_state.login_status = ""


if "monitorando" not in st.session_state:

    st.session_state.monitorando = True


if "ultima_execucao" not in st.session_state:

    st.session_state.ultima_execucao = None


if "ultima_transferencias" not in st.session_state:

    st.session_state.ultima_transferencias = []


if "total_ciclos" not in st.session_state:

    st.session_state.total_ciclos = 0


if "confirmar_limpeza_temp" not in st.session_state:

    st.session_state.confirmar_limpeza_temp = False


if "confirmar_exclusao_auditoria" not in st.session_state:

    st.session_state.confirmar_exclusao_auditoria = False


if "limpeza_temp_resultado" not in st.session_state:

    st.session_state.limpeza_temp_resultado = None


# ============================================================
# SIDEBAR
# ============================================================

st.sidebar.title(
    "⚙️ Configurações"
)


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


st.sidebar.divider()


# ============================================================
# LIMPEZA
# ============================================================

st.sidebar.subheader(
    "🧹 Limpeza"
)


st.sidebar.caption(
    "Remove registros antigos do estado temporário. "
    "Atendimentos que ainda estão abertos são preservados."
)


data_limpeza = st.sidebar.date_input(
    "Limpar estado entre:",
    value=date.today() - timedelta(days=30),
    key="data_limpeza_temp"
)


data_limpeza_fim = st.sidebar.date_input(
    "e:",
    value=date.today(),
    key="data_limpeza_temp_fim"
)


if st.sidebar.button(
    "🧹 Limpar estado temporário",
    use_container_width=True
):

    try:

        tickets_abertos = buscar_tickets_abertos(
            st.session_state.whats_session
        )


        ids_abertos = {

            str(t.get("id"))

            for t in tickets_abertos

            if t.get("id")
        }


    except Exception as e:

        st.sidebar.error(
            f"Erro ao consultar tickets abertos: {e}"
        )

        ids_abertos = set()


    resultado_limpeza = limpar_estado_temporario(

        data_inicial=data_limpeza,

        data_final=data_limpeza_fim,

        ids_abertos=ids_abertos,

        confirmar=True
    )


    if resultado_limpeza["status"] == "OK":

        st.sidebar.success(
            f"✅ {resultado_limpeza['removidos']} "
            f"registros temporários removidos."
        )


        if resultado_limpeza["abertos"] > 0:

            st.sidebar.info(
                f"🔒 {resultado_limpeza['abertos']} "
                f"atendimentos ainda abertos foram preservados."
            )


# ============================================================
# TÍTULO
# ============================================================

st.title(
    "🔎 Auditoria de Transferências WhatsFlux"
)


st.caption(
    "Monitora responsáveis e registra somente alterações de técnico."
)


# ============================================================
# LOGIN
# ============================================================

if st.session_state.whats_session is None:

    session, mensagem = criar_sessao_whatsflux()


    if session:

        st.session_state.whats_session = session

        st.session_state.login_status = "OK"

    else:

        st.session_state.login_status = mensagem


if st.session_state.whats_session is None:

    st.error(
        f"❌ {st.session_state.login_status}"
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

    session = st.session_state.whats_session


    # ========================================================
    # EXECUTA MONITORAMENTO
    # ========================================================

    try:

        resultado = executar_monitoramento(
            session
        )


        st.session_state.total_ciclos += 1


        st.session_state.ultima_execucao = agora()


        st.session_state.ultima_transferencias = (
            resultado.get(
                "transferencias",
                []
            )
        )


        transferencias = resultado.get(
            "transferencias",
            []
        )


        if transferencias:

            for transferencia in transferencias:

                st.toast(
                    (
                        "🔄 Transferência detectada: "
                        f"{transferencia['anterior']} → "
                        f"{transferencia['atual']} "
                        f"(Ticket #{transferencia['ticket_id']})"
                    ),
                    icon="🔎"
                )


    except Exception as e:

        st.error(
            f"❌ Erro no monitoramento: {e}"
        )

        return


    # ========================================================
    # DADOS
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


    total_auditoria = quantidade_auditoria()


    # ========================================================
    # CARDS SUPERIORES
    # ========================================================
    #
    # Aqui está a principal alteração.
    #
    # Não usamos mais:
    #
    # <div class="metric-card">
    #
    # Portanto o HTML não será exibido na página.
    #
    # ========================================================

    col1, col2, col3, col4 = st.columns(
        4,
        gap="medium"
    )


    with col1:

        st.metric(
            label="🎫 Tickets abertos",
            value=tickets_abertos
        )


    with col2:

        st.metric(
            label="👥 Em monitoramento",
            value=len(estado)
        )


    with col3:

        st.metric(
            label="👤 Novos responsáveis",
            value=entradas
        )


    with col4:

        st.metric(
            label="🔄 Transferências auditadas",
            value=total_auditoria
        )


    # ========================================================
    # STATUS
    # ========================================================

    ultima = st.session_state.ultima_execucao


    if ultima:

        horario = ultima.strftime(
            "%d/%m/%Y %H:%M:%S"
        )

    else:

        horario = "-"


    if st.session_state.monitorando:

        status_texto = (
            f"🟢 **Monitoramento ativo** | "
            f"Última consulta: **{horario}** | "
            f"Ciclos executados: **{st.session_state.total_ciclos}**"
        )

        st.success(
            status_texto,
            icon="🟢"
        )

    else:

        status_texto = (
            f"⏸️ **Monitoramento pausado** | "
            f"Última consulta: **{horario}** | "
            f"Ciclos executados: **{st.session_state.total_ciclos}**"
        )

        st.warning(
            status_texto,
            icon="⏸️"
        )


    # ========================================================
    # TRANSFERÊNCIAS DETECTADAS
    # ========================================================

    if transferencias:

        st.subheader(
            "🔄 Transferências detectadas neste ciclo"
        )


        for item in transferencias:

            with st.container(
                border=True
            ):

                st.write(
                    f"**Ticket #{item['ticket_id']}**"
                )


                if item.get("cliente"):

                    st.caption(
                        f"Cliente: {item['cliente']}"
                    )


                col_a, col_b, col_c = st.columns(
                    [2, 1, 2]
                )


                with col_a:

                    st.write(
                        f"👤 **{item['anterior']}**"
                    )


                with col_b:

                    st.write(
                        "➡️"
                    )


                with col_c:

                    st.write(
                        f"**{item['atual']}**"
                    )


    else:

        st.info(
            "Nenhuma transferência detectada neste ciclo."
        )


    # ========================================================
    # ESTADO ATUAL DOS ATENDIMENTOS
    # ========================================================

    with st.expander(
        "👥 Atendimentos atualmente monitorados",
        expanded=False
    ):

        if estado:

            linhas = []


            for item in estado.values():

                linhas.append(
                    {

                        "Ticket": item.get(
                            "ticket_id"
                        ),

                        "Cliente": item.get(
                            "cliente"
                        ),

                        "Telefone": item.get(
                            "telefone"
                        ),

                        "Técnico": item.get(
                            "tecnico"
                        ),

                        "Detectado em": formatar_data_hora(
                            item.get(
                                "detectado_em"
                            )
                        )
                    }
                )


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
    # EXPORTAÇÃO DA AUDITORIA
    # ========================================================

    st.subheader(
        "📄 Exportar auditoria"
    )


    st.caption(
        "Exporte as transferências registradas no banco "
        "de auditoria. Mesmo sem registros, é possível "
        "gerar um CSV contendo apenas os cabeçalhos."
    )


    col_csv1, col_csv2 = st.columns(
        2
    )


    with col_csv1:

        data_csv_inicial = st.date_input(
            "Data inicial",
            value=date.today() - timedelta(days=30),
            key="data_csv_inicial"
        )


    with col_csv2:

        data_csv_final = st.date_input(
            "Data final",
            value=date.today(),
            key="data_csv_final"
        )


    # ========================================================
    # VALIDAÇÃO
    # ========================================================

    if data_csv_inicial > data_csv_final:

        st.error(
            "❌ A data inicial não pode ser maior "
            "que a data final."
        )

    else:

        df_csv = consultar_auditoria(

            data_inicial=data_csv_inicial,

            data_final=data_csv_final,

            tecnico="Todos"
        )


        if df_csv.empty:

            st.info(
                "ℹ️ Nenhuma transferência encontrada "
                "no período. O CSV conterá apenas "
                "os cabeçalhos."
            )

        else:

            st.success(
                f"✅ {len(df_csv)} transferência(s) "
                f"encontrada(s) no período."
            )


        arquivo_csv = gerar_csv(
            df_csv
        )


        nome_arquivo = (

            "auditoria_transferencias_"

            f"{data_csv_inicial.strftime('%Y%m%d')}_"

            f"{data_csv_final.strftime('%Y%m%d')}.csv"
        )


        st.download_button(

            label="📥 Baixar CSV da auditoria",

            data=arquivo_csv,

            file_name=nome_arquivo,

            mime="text/csv",

            use_container_width=True
        )


# ============================================================
# EXECUTA O MONITORAMENTO
# ============================================================

painel_monitoramento()
