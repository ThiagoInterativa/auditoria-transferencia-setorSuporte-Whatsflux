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

st.markdown("""
<style>

.main-title {
    font-size: 30px;
    font-weight: 700;
    margin-bottom: 5px;
}

.subtitle {
    color: #64748b;
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
""", unsafe_allow_html=True)


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

        return dt.astimezone(TZ).strftime("%d/%m/%Y %H:%M:%S")

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
    """
    Carrega o estado atual dos atendimentos monitorados.

    IMPORTANTE:
    Este arquivo NÃO é o banco de auditoria.

    Ele serve apenas para o sistema saber:
        Ticket 10130 -> atualmente Thiago

    Assim conseguimos detectar:
        Thiago -> Gabriel
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
    Salva de forma relativamente segura usando arquivo temporário.
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
    conn = sqlite3.connect(
        AUDIT_DB_FILE,
        timeout=30
    )

    conn.row_factory = sqlite3.Row

    return conn


def inicializar_banco():

    conn = conectar_banco()

    conn.execute("""
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
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_auditoria_ticket
        ON auditoria(ticket_id)
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_auditoria_data
        ON auditoria(data_hora)
    """)

    conn.commit()
    conn.close()


# ============================================================
# CONSULTAR ÚLTIMO EVENTO DO TICKET
# ============================================================

def buscar_ultimo_evento(ticket_id):

    conn = conectar_banco()

    cursor = conn.execute("""
        SELECT *
        FROM auditoria
        WHERE ticket_id = ?
        ORDER BY id DESC
        LIMIT 1
    """, (ticket_id,))

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

    conn.execute("""
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
    """, (
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
    ))

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

        return None, "Configure WHATSFLUX_EMAIL e WHATSFLUX_SENHA nos Secrets."


    session = requests.Session()

    session.headers.update({

        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),

        "Accept": "application/json, text/plain, */*",

        "Content-Type": "application/json;charset=UTF-8",

        "Referer": "https://app.whatsflux.com.br/",

        "Origin": "https://app.whatsflux.com.br"
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

            session.headers.update({
                "Authorization": f"Bearer {token}"
            })

        else:

            # Algumas APIs podem usar cookie de sessão.
            # Nesse caso a própria Session continuará carregando
            # os cookies recebidos.

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


        has_more = dados.get("hasMore", False)

        if not has_more:

            break


        page_number += 1


        # Segurança contra alguma API que fique retornando
        # hasMore=true indefinidamente.
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
    """
    Regra principal:

    1. Sem técnico -> ignora.
    2. Primeira vez com técnico -> apenas coloca no estado temporário.
    3. Mesmo técnico -> não faz nada.
    4. Técnico diferente -> grava TRANSFERENCIA.
    5. Atualiza o estado temporário.
    """

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


    tecnico_anterior_id = anterior.get("tecnico_id")
    tecnico_anterior = anterior.get("tecnico")


    # ========================================================
    # MESMO TÉCNICO
    # ========================================================

    if str(tecnico_anterior_id) == str(user_id):

        # Atualizamos somente informações auxiliares.
        # Não criamos evento de auditoria.

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

    tickets = buscar_tickets_abertos(session)

    transferencias = []

    entradas = 0

    ignorados = 0


    # IDs atualmente abertos
    ids_abertos = set()


    for ticket in tickets:

        ticket_id = ticket.get("id")

        if ticket_id:

            ids_abertos.add(str(ticket_id))


        resultado = processar_ticket(
            ticket,
            estado
        )


        if not resultado:

            continue


        if resultado["tipo"] == "ENTRADA_MONITORAMENTO":

            entradas += 1


        elif resultado["tipo"] == "TRANSFERENCIA":

            transferencias.append(resultado)


    # ========================================================
    # SALVA O ESTADO
    # ========================================================

    salvar_estado_temporario(estado)


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

    inicio = data_inicio(data_inicial).isoformat()
    fim = data_fim(data_final).isoformat()


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
# TÉCNICOS EXISTENTES NA AUDITORIA
# ============================================================

def listar_tecnicos_auditoria():

    conn = conectar_banco()


    df = pd.read_sql_query("""
        SELECT tecnico_anterior AS tecnico
        FROM auditoria

        WHERE tecnico_anterior IS NOT NULL

        UNION

        SELECT tecnico_atual AS tecnico
        FROM auditoria

        WHERE tecnico_atual IS NOT NULL

        ORDER BY tecnico
    """, conn)


    conn.close()


    if df.empty:

        return []


    return df["tecnico"].dropna().tolist()


# ============================================================
# ESTATÍSTICAS
# ============================================================

def quantidade_auditoria():

    conn = conectar_banco()

    cursor = conn.execute("""
        SELECT COUNT(*)
        FROM auditoria
    """)

    quantidade = cursor.fetchone()[0]

    conn.close()

    return quantidade


# ============================================================
# ESTADO TEMPORÁRIO - DATA
# ============================================================

def data_estado(item):

    valor = item.get("detectado_em")

    try:

        dt = datetime.fromisoformat(valor)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)

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

    inicio = data_inicio(data_inicial)
    fim = data_fim(data_final)

    encontrados = []


    for chave, item in estado.items():

        dt = data_estado(item)

        if not dt:
            continue


        # O registro pertence ao período selecionado?
        dentro_periodo = (
            inicio <= dt <= fim
        )


        if not dentro_periodo:
            continue


        # E o ticket ainda está aberto?
        if str(chave) in ids_abertos:

            encontrados.append(item)


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

    inicio = data_inicio(data_inicial)
    fim = data_fim(data_final)


    candidatos = []

    abertos_no_periodo = []


    for chave, item in estado.items():

        dt = data_estado(item)

        if not dt:
            continue


        if not (inicio <= dt <= fim):
            continue


        if str(chave) in ids_abertos:

            abertos_no_periodo.append(item)

        else:

            candidatos.append(chave)


    # --------------------------------------------------------
    # Segurança
    # --------------------------------------------------------

    if abertos_no_periodo and not confirmar:

        return {
            "status": "CONFIRMACAO_NECESSARIA",

            "removiveis": len(candidatos),

            "abertos": abertos_no_periodo
        }


    # --------------------------------------------------------
    # Exclusão
    # --------------------------------------------------------

    for chave in candidatos:

        estado.pop(chave, None)


    salvar_estado_temporario(estado)


    return {
        "status": "OK",

        "removidos": len(candidatos),

        "abertos": len(abertos_no_periodo)
    }


# ============================================================
# EXCLUIR AUDITORIA
# ============================================================

def excluir_auditoria_periodo(
    data_inicial,
    data_final
):

    inicio = data_inicio(data_inicial).isoformat()
    fim = data_fim(data_final).isoformat()


    conn = conectar_banco()


    cursor = conn.execute("""
        DELETE FROM auditoria

        WHERE data_hora >= ?
        AND data_hora <= ?
    """, (
        inicio,
        fim
    ))


    removidos = cursor.rowcount

    conn.commit()

    conn.close()


    return removidos


# ============================================================
# CSV
# ============================================================

def gerar_csv(df):

    if df.empty:

        return b""


    buffer = io.StringIO()


    df.to_csv(
        buffer,
        index=False,
        sep=";",
        encoding="utf-8-sig"
    )


    return buffer.getvalue().encode("utf-8-sig")


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

st.sidebar.title("⚙️ Configurações")


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
    disabled=st.session_state.monitorando
):

    st.session_state.monitorando = True

    st.rerun()


if st.sidebar.button(
    "⏸️ Pausar monitoramento",
    disabled=not st.session_state.monitorando
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

    st.sidebar.success("🟢 Monitoramento ativo")

else:

    st.sidebar.warning("⏸️ Monitoramento pausado")


# ============================================================
# TÍTULO
# ============================================================

st.markdown(
    '<div class="main-title">🔎 Auditoria de Transferências WhatsFlux</div>',
    unsafe_allow_html=True
)

st.markdown(
    '<div class="subtitle">'
    'Monitora responsáveis e registra somente alterações de técnico.'
    '</div>',
    unsafe_allow_html=True
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

run_every = intervalo if st.session_state.monitorando else None


@st.fragment(run_every=run_every, key="monitor_whatsflux")
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
            resultado["transferencias"]
        )


        # ====================================================
        # MÉTRICAS
        # ====================================================

        estado = resultado["estado"]

        col1, col2, col3, col4 = st.columns(4)


        with col1:

            st.markdown(
                f"""
                <div class="metric-card">
                    <div class="metric-number">
                        {resultado["tickets_abertos"]}
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
                        {len(resultado["transferencias"])}
                    </div>
                    <div class="metric-label">
                        Transferências neste ciclo
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
                        Eventos de auditoria
                    </div>
                </div>
                """,
                unsafe_allow_html=True
            )


        st.write("")


        # ====================================================
        # STATUS
        # ====================================================

        col_status, col_hora = st.columns([2, 2])


        with col_status:

            st.success(
                "🟢 Monitoramento funcionando"
            )


        with col_hora:

            if st.session_state.ultima_execucao:

                st.caption(
                    "Última consulta: "
                    + st.session_state.ultima_execucao.strftime(
                        "%d/%m/%Y %H:%M:%S"
                    )
                )


        # ====================================================
        # TRANSFERÊNCIAS DETECTADAS
        # ====================================================

        if resultado["transferencias"]:

            st.subheader(
                "🚨 Transferências detectadas neste ciclo"
            )


            for item in resultado["transferencias"]:

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


        # ====================================================
        # ESTADO ATUAL
        # ====================================================

        st.subheader(
            "👥 Atendimentos atualmente monitorados"
        )


        if estado:

            linhas = []


            for item in estado.values():

                linhas.append({

                    "Ticket": item.get("ticket_id"),

                    "Cliente": item.get("cliente"),

                    "Telefone": item.get("telefone"),

                    "Técnico": item.get("tecnico"),

                    "ID Técnico": item.get("tecnico_id"),

                    "Detectado em": formatar_data_hora(
                        item.get("detectado_em")
                    )
                })


            df_estado = pd.DataFrame(linhas)

            st.dataframe(
                df_estado,
                use_container_width=True,
                hide_index=True
            )

        else:

            st.info(
                "Nenhum atendimento com técnico está sendo monitorado."
            )


        # ====================================================
        # MANUTENÇÃO
        # ====================================================

        st.divider()

        st.subheader(
            "🧹 Manutenção do estado temporário"
        )


        estado_atual = carregar_estado_temporario()


        st.write(
            f"Existem **{len(estado_atual)}** "
            "atendimentos no estado temporário."
        )


        col_data1, col_data2 = st.columns(2)


        with col_data1:

            data_limpeza_inicio = st.date_input(
                "Limpar a partir de",
                value=date.today() - timedelta(days=30),
                key="data_limpeza_inicio"
            )


        with col_data2:

            data_limpeza_fim = st.date_input(
                "Até",
                value=date.today(),
                key="data_limpeza_fim"
            )


        if data_limpeza_inicio > data_limpeza_fim:

            st.error(
                "A data inicial não pode ser maior que a final."
            )

        else:

            ids_abertos = resultado["ids_abertos"]


            if st.button(
                "🧹 Verificar e limpar estado temporário",
                key="btn_limpar_temp"
            ):

                resultado_limpeza = limpar_estado_temporario(
                    data_limpeza_inicio,
                    data_limpeza_fim,
                    ids_abertos,
                    confirmar=False
                )


                if resultado_limpeza["status"] == "CONFIRMACAO_NECESSARIA":

                    st.session_state.confirmar_limpeza_temp = True

                    st.session_state.limpeza_temp_resultado = (
                        resultado_limpeza
                    )

                else:

                    st.success(
                        f'✅ {resultado_limpeza["removidos"]} '
                        "registros temporários removidos."
                    )

                    st.session_state.confirmar_limpeza_temp = False


            # =================================================
            # CONFIRMAÇÃO
            # =================================================

            if st.session_state.confirmar_limpeza_temp:

                dados = (
                    st.session_state.limpeza_temp_resultado
                )


                st.warning(
                    f"""
                    ⚠️ Existem **{len(dados["abertos"])}**
                    atendimentos ainda abertos dentro do
                    período selecionado.

                    Se você limpar o estado desses atendimentos,
                    o sistema poderá perder a referência necessária
                    para detectar uma futura alteração de técnico.

                    Deseja realmente continuar?
                    """
                )


                for item in dados["abertos"]:

                    st.write(
                        f'- Ticket **{item.get("ticket_id")}** '
                        f'| {item.get("cliente")} '
                        f'| Técnico: **{item.get("tecnico")}**'
                    )


                col_conf1, col_conf2 = st.columns(2)


                with col_conf1:

                    if st.button(
                        "⚠️ SIM, LIMPAR MESMO ASSIM",
                        key="confirmar_limpeza"
                    ):

                        resultado_final = limpar_estado_temporario(
                            data_limpeza_inicio,
                            data_limpeza_fim,
                            ids_abertos,
                            confirmar=True
                        )


                        st.session_state.confirmar_limpeza_temp = False

                        st.session_state.limpeza_temp_resultado = None


                        st.success(
                            f'✅ {resultado_final["removidos"]} '
                            "registros removidos."
                        )

                        st.rerun(scope="fragment")


                with col_conf2:

                    if st.button(
                        "❌ Cancelar",
                        key="cancelar_limpeza"
                    ):

                        st.session_state.confirmar_limpeza_temp = False

                        st.session_state.limpeza_temp_resultado = None

                        st.rerun(scope="fragment")


        # ====================================================
        # AUDITORIA
        # ====================================================

        st.divider()

        st.subheader(
            "📊 Relatório de auditoria"
        )


        col_data1, col_data2 = st.columns(2)


        with col_data1:

            data_relatorio_inicio = st.date_input(
                "Data inicial",
                value=date.today(),
                key="relatorio_inicio"
            )


        with col_data2:

            data_relatorio_fim = st.date_input(
                "Data final",
                value=date.today(),
                key="relatorio_fim"
            )


        tecnicos = [
            "Todos"
        ] + listar_tecnicos_auditoria()


        tecnico_filtro = st.selectbox(
            "Filtrar por técnico",
            tecnicos,
            key="filtro_tecnico"
        )


        if data_relatorio_inicio > data_relatorio_fim:

            st.error(
                "A data inicial não pode ser maior que a final."
            )

        else:

            df_auditoria = consultar_auditoria(
                data_relatorio_inicio,
                data_relatorio_fim,
                tecnico_filtro
            )


            col1, col2, col3 = st.columns(3)


            with col1:

                st.metric(
                    "Eventos no período",
                    len(df_auditoria)
                )


            with col2:

                if not df_auditoria.empty:

                    st.metric(
                        "Tickets envolvidos",
                        df_auditoria["ticket_id"].nunique()
                    )

                else:

                    st.metric(
                        "Tickets envolvidos",
                        0
                    )


            with col3:

                if not df_auditoria.empty:

                    st.metric(
                        "Transferências",
                        (
                            df_auditoria["evento"]
                            == "TRANSFERENCIA"
                        ).sum()
                    )

                else:

                    st.metric(
                        "Transferências",
                        0
                    )


            if not df_auditoria.empty:

                df_visual = df_auditoria.copy()


                df_visual["data_hora"] = (
                    pd.to_datetime(
                        df_visual["data_hora"],
                        errors="coerce"
                    )
                    .dt.strftime("%d/%m/%Y %H:%M:%S")
                )


                df_visual = df_visual.rename(columns={

                    "id": "ID",

                    "ticket_id": "Ticket",

                    "ticket_uuid": "UUID",

                    "contact_id": "ID Cliente",

                    "cliente": "Cliente",

                    "telefone": "Telefone",

                    "tecnico_anterior_id": "ID Técnico Anterior",

                    "tecnico_anterior": "Técnico Anterior",

                    "tecnico_atual_id": "ID Técnico Atual",

                    "tecnico_atual": "Técnico Atual",

                    "evento": "Evento",

                    "data_hora": "Data/Hora"
                })


                st.dataframe(
                    df_visual,
                    use_container_width=True,
                    hide_index=True
                )


                # =============================================
                # CSV
                # =============================================

                csv_data = gerar_csv(
                    df_visual
                )


                nome_arquivo = (
                    "auditoria_"
                    f"{data_relatorio_inicio.strftime('%Y%m%d')}_"
                    f"{data_relatorio_fim.strftime('%Y%m%d')}.csv"
                )


                st.download_button(
                    "📥 Exportar relatório CSV",
                    data=csv_data,
                    file_name=nome_arquivo,
                    mime="text/csv",
                    key="download_csv",
                    width="stretch"
                )


            else:

                st.info(
                    "Nenhum evento encontrado no período."
                )


        # ====================================================
        # EXCLUSÃO MANUAL DO BANCO DE AUDITORIA
        # ====================================================

        st.divider()

        st.subheader(
            "🗑️ Exclusão manual do banco de auditoria"
        )


        st.warning(
            "Esta operação é separada da limpeza do estado temporário. "
            "Ela exclui registros PERMANENTES da auditoria."
        )


        col_data1, col_data2 = st.columns(2)


        with col_data1:

            data_exclusao_inicio = st.date_input(
                "Excluir a partir de",
                value=date.today() - timedelta(days=30),
                key="exclusao_inicio"
            )


        with col_data2:

            data_exclusao_fim = st.date_input(
                "Excluir até",
                value=date.today(),
                key="exclusao_fim"
            )


        if data_exclusao_inicio > data_exclusao_fim:

            st.error(
                "A data inicial não pode ser maior que a final."
            )

        else:

            df_exclusao = consultar_auditoria(
                data_exclusao_inicio,
                data_exclusao_fim
            )


            quantidade_exclusao = len(df_exclusao)


            st.write(
                f"Serão excluídos **{quantidade_exclusao}** "
                "eventos de auditoria."
            )


            if st.button(
                "🗑️ Solicitar exclusão da auditoria",
                key="btn_solicitar_exclusao"
            ):

                if quantidade_exclusao == 0:

                    st.info(
                        "Não existem registros nesse período."
                    )

                else:

                    st.session_state.confirmar_exclusao_auditoria = True


            # =============================================
            # CONFIRMAÇÃO
            # =============================================

            if st.session_state.confirmar_exclusao_auditoria:

                st.error(
                    f"""
                    ⚠️ ATENÇÃO

                    Você está prestes a excluir
                    **{quantidade_exclusao} registros**
                    do banco de auditoria.

                    Essa exclusão é permanente.
                    """
                )


                col_conf1, col_conf2 = st.columns(2)


                with col_conf1:

                    if st.button(
                        "🚨 CONFIRMAR EXCLUSÃO PERMANENTE",
                        key="confirmar_exclusao_final"
                    ):

                        removidos = excluir_auditoria_periodo(
                            data_exclusao_inicio,
                            data_exclusao_fim
                        )


                        st.session_state.confirmar_exclusao_auditoria = False


                        st.success(
                            f"✅ {removidos} registros excluídos."
                        )


                        st.rerun(scope="fragment")


                with col_conf2:

                    if st.button(
                        "❌ Cancelar",
                        key="cancelar_exclusao"
                    ):

                        st.session_state.confirmar_exclusao_auditoria = False

                        st.rerun(scope="fragment")


# ============================================================
# EXECUTA O PAINEL
# ============================================================

painel_monitoramento()
