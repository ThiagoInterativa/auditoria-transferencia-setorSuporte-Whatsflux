import streamlit as st
import requests
import sqlite3
import json
import os
import io
import pandas as pd

from datetime import datetime, date
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

# Arquivo que guarda somente o estado atual dos tickets
TEMP_STATE_FILE = "estado_monitoramento.json"

# Banco principal da auditoria
AUDIT_DB_FILE = "auditoria.db"

# Fuso horário
TZ = ZoneInfo("America/Sao_Paulo")

# Intervalo padrão
DEFAULT_INTERVAL = 2.0


# ============================================================
# FUNÇÕES DE DATA/HORA
# ============================================================

def agora():
    """Retorna data/hora atual no horário de São Paulo."""
    return datetime.now(TZ)


def agora_iso():
    """Retorna data/hora atual em formato ISO."""
    return agora().isoformat(timespec="seconds")


def formatar_data_hora(valor):
    """
    Converte uma data ISO para o formato brasileiro.
    """

    if not valor:
        return ""

    try:
        dt = datetime.fromisoformat(str(valor))

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)

        return dt.astimezone(TZ).strftime(
            "%d/%m/%Y %H:%M:%S"
        )

    except Exception:
        return str(valor)


def data_inicio(d):
    """Início do dia."""
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
    """Final do dia."""
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
    Carrega os atendimentos que estão atualmente sendo
    acompanhados.

    IMPORTANTE:

    Este arquivo NÃO é a auditoria.

    Exemplo:

    ticket 10130
        técnico atual = Thiago

    Na próxima consulta:

    ticket 10130
        técnico atual = Gabriel

    O sistema detecta:

        Thiago -> Gabriel

    E somente então grava no banco de auditoria.
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

    Usa arquivo .tmp para reduzir o risco de corromper
    o arquivo caso o processo seja interrompido durante
    a gravação.
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
    Cria a tabela de auditoria caso ela ainda não exista.
    """

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
    Grava uma transferência no banco principal.

    Exemplo:

        Ticket: 10130
        Cliente: 36202346487953
        Anterior: Thiago
        Atual: Gabriel
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

        VALUES (
            ?, ?, ?, ?, ?,
            ?, ?,
            ?, ?,
            ?, ?
        )
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

        return (
            None,
            "Configure WHATSFLUX_EMAIL e WHATSFLUX_SENHA nos Secrets."
        )


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


        if response.status_code not in [
            200,
            201,
            202
        ]:

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


        elif not session.cookies:

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
                "Sessão expirada. Será necessário fazer login novamente."
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


        # Segurança
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
    Regra principal da auditoria.

    PRIMEIRA VEZ:

        Ticket -> Thiago

    Não grava no banco.

    Apenas:

        estado temporário
        Ticket -> Thiago


    PRÓXIMA CONSULTA:

        Ticket -> Thiago

    Nada acontece.


    SE MUDAR:

        Ticket -> Gabriel

    Grava:

        anterior = Thiago
        atual    = Gabriel


    Depois o estado temporário passa a ser:

        Ticket -> Gabriel
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


    # Atualiza o estado temporário
    estado[chave] = dados_atuais


    return {

        "tipo": "TRANSFERENCIA",

        "ticket_id": ticket_id,

        "cliente": cliente,

        "anterior": tecnico_anterior,

        "atual": tecnico_atual
    }


# ============================================================
# EXECUTAR MONITORAMENTO
# ============================================================

def executar_monitoramento(session):

    estado = carregar_estado_temporario()


    tickets = buscar_tickets_abertos(
        session
    )


    transferencias = []


    entradas = 0


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


        if resultado["tipo"] == "ENTRADA_MONITORAMENTO":

            entradas += 1


        elif resultado["tipo"] == "TRANSFERENCIA":

            transferencias.append(
                resultado
            )


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
# CONSULTAR AUDITORIA
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
# LISTAR TÉCNICOS
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


    return (
        df["tecnico"]
        .dropna()
        .tolist()
    )


# ============================================================
# QUANTIDADE DE AUDITORIAS
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
# DATA DO ESTADO TEMPORÁRIO
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
# LIMPAR ESTADO TEMPORÁRIO
# ============================================================

def limpar_estado_temporario(
    data_inicial,
    data_final,
    ids_abertos,
    confirmar=False
):
    """
    Remove do estado temporário somente tickets
    que NÃO estão mais abertos.

    Isso evita apagar um ticket ainda em atendimento.

    Exemplo:

        Estado:

        10130 -> Thiago
        10131 -> Gabriel

    Se 10130 ainda estiver aberto:

        NÃO apaga 10130.

    Se 10131 já fechou:

        pode apagar 10131.
    """

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


    # ========================================================
    # CONFIRMAÇÃO
    # ========================================================

    if abertos_no_periodo and not confirmar:

        return {

            "status":
                "CONFIRMACAO_NECESSARIA",

            "removiveis":
                len(candidatos),

            "abertos":
                abertos_no_periodo
        }


    # ========================================================
    # EXCLUSÃO
    # ========================================================

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

        "removidos":
            len(candidatos),

        "abertos":
            len(abertos_no_periodo)
    }


# ============================================================
# EXCLUIR AUDITORIA POR PERÍODO
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
# GERAR CSV
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


if "ultimo_resultado" not in st.session_state:

    st.session_state.ultimo_resultado = {

        "tickets_abertos": 0,

        "ids_abertos": set(),

        "estado": {},

        "entradas": 0,

        "transferencias": []
    }


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
    "O padrão é consultar a API a cada 2 segundos."
)


st.sidebar.divider()


# ============================================================
# CONTROLES DO MONITORAMENTO
# ============================================================

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
    f"**Intervalo:** {intervalo:.1f} segundos"
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
# FUNÇÃO DO PAINEL DE MONITORAMENTO
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
    # EXECUTA MONITORAMENTO
    # ========================================================

    if st.session_state.monitorando:

        try:

            resultado = executar_monitoramento(
                st.session_state.whats_session
            )


            # Salva resultado atual
            st.session_state.ultimo_resultado = resultado


            # Atualiza informações
            st.session_state.ultima_execucao = agora_iso()


            st.session_state.total_ciclos += 1


            st.session_state.ultima_transferencias = (
                resultado["transferencias"]
            )


        except RuntimeError as erro:

            mensagem = str(erro)


            # =================================================
            # SESSÃO EXPIRADA
            # =================================================

            if "401" in mensagem or "Sessão expirada" in mensagem:

                st.session_state.whats_session = None


                nova_sessao, nova_mensagem = (
                    criar_sessao_whatsflux()
                )


                if nova_sessao:

                    st.session_state.whats_session = (
                        nova_sessao
                    )

                    st.session_state.login_status = "OK"


                else:

                    st.error(
                        f"❌ {nova_mensagem}"
                    )


            else:

                st.error(
                    f"❌ Erro no monitoramento: {mensagem}"
                )


        except Exception as erro:

            st.error(
                f"❌ Erro inesperado: {erro}"
            )


    # ========================================================
    # RESULTADO ATUAL
    # ========================================================

    resultado = st.session_state.ultimo_resultado


    estado_atual = (
        resultado.get("estado")
        or carregar_estado_temporario()
    )


    tickets_abertos = resultado.get(
        "tickets_abertos",
        0
    )


    entradas = resultado.get(
        "entradas",
        0
    )


    transferencias_ciclo = (
        resultado.get("transferencias")
        or []
    )


    total_auditoria = quantidade_auditoria()


    # ========================================================
    # INDICADORES
    #
    # IMPORTANTE:
    # Não usamos HTML aqui.
    #
    # Isso elimina o problema dos:
    #
    # <div>
    # <strong>
    # etc.
    # ========================================================

    col1, col2, col3, col4 = st.columns(4)


    with col1:

        st.metric(
            "🎫 Tickets abertos",
            tickets_abertos
        )


    with col2:

        st.metric(
            "👁️ Em monitoramento",
            len(estado_atual)
        )


    with col3:

        st.metric(
            "👤 Novos responsáveis",
            entradas
        )


    with col4:

        st.metric(
            "🔄 Transferências auditadas",
            total_auditoria
        )


    # ========================================================
    # STATUS
    # ========================================================

    st.divider()


    col_status, col_hora, col_ciclos = st.columns(3)


    with col_status:

        if st.session_state.monitorando:

            st.success(
                "🟢 Monitoramento ativo"
            )

        else:

            st.warning(
                "⏸️ Monitoramento pausado"
            )


    with col_hora:

        st.write(
            "**Última consulta:**"
        )


        if st.session_state.ultima_execucao:

            st.write(
                formatar_data_hora(
                    st.session_state.ultima_execucao
                )
            )

        else:

            st.write(
                "Aguardando..."
            )


    with col_ciclos:

        st.write(
            "**Ciclos executados:**"
        )


        st.write(
            st.session_state.total_ciclos
        )


    # ========================================================
    # TRANSFERÊNCIAS DETECTADAS NESTE CICLO
    # ========================================================

    st.divider()


    st.subheader(
        "🔄 Transferências detectadas neste ciclo"
    )


    if transferencias_ciclo:

        for transferencia in transferencias_ciclo:

            ticket_id = transferencia.get(
                "ticket_id",
                ""
            )

            cliente = transferencia.get(
                "cliente",
                ""
            )

            anterior = transferencia.get(
                "anterior",
                ""
            )

            atual = transferencia.get(
                "atual",
                ""
            )


            with st.container(border=True):

                c1, c2, c3, c4 = st.columns(
                    [1, 2, 2, 2]
                )


                with c1:

                    st.write(
                        f"**Ticket:** {ticket_id}"
                    )


                with c2:

                    st.write(
                        f"**Cliente:** {cliente}"
                    )


                with c3:

                    st.write(
                        f"**Anterior:** {anterior}"
                    )


                with c4:

                    st.write(
                        f"**Atual:** {atual}"
                    )


    else:

        st.info(
            "Nenhuma transferência detectada neste ciclo."
        )


    # ========================================================
    # ATENDIMENTOS ATUALMENTE MONITORADOS
    # ========================================================

    st.divider()


    st.subheader(
        "👥 Atendimentos atualmente monitorados"
    )


    if estado_atual:

        lista_monitorados = []


        for chave, item in estado_atual.items():

            lista_monitorados.append({

                "Ticket ID":
                    item.get("ticket_id"),

                "Cliente":
                    item.get("cliente"),

                "Telefone":
                    item.get("telefone"),

                "Técnico atual":
                    item.get("tecnico"),

                "ID técnico":
                    item.get("tecnico_id"),

                "Detectado em":
                    formatar_data_hora(
                        item.get("detectado_em")
                    )
            })


        df_monitorados = pd.DataFrame(
            lista_monitorados
        )


        df_monitorados = (
            df_monitorados
            .sort_values(
                "Detectado em",
                ascending=False
            )
        )


        st.dataframe(
            df_monitorados,
            use_container_width=True,
            hide_index=True
        )


    else:

        st.info(
            "Nenhum atendimento com técnico está sendo monitorado."
        )


# ============================================================
# EXECUTA O PAINEL
# ============================================================

painel_monitoramento()


# ============================================================
# HISTÓRICO DE TRANSFERÊNCIAS
# ============================================================

st.divider()


st.header(
    "📊 Histórico de Transferências"
)


# ============================================================
# FILTROS
# ============================================================

col_data1, col_data2, col_tecnico = st.columns(
    [1, 1, 1]
)


with col_data1:

    data_inicial = st.date_input(
        "Data inicial",
        value=date.today(),
        key="filtro_data_inicial"
    )


with col_data2:

    data_final = st.date_input(
        "Data final",
        value=date.today(),
        key="filtro_data_final"
    )


with col_tecnico:

    tecnicos = listar_tecnicos_auditoria()


    tecnico_filtro = st.selectbox(
        "Técnico",
        ["Todos"] + tecnicos,
        key="filtro_tecnico"
    )


# ============================================================
# VALIDAÇÃO DE DATAS
# ============================================================

if data_inicial > data_final:

    st.error(
        "A data inicial não pode ser maior que a data final."
    )

    st.stop()


# ============================================================
# CONSULTAR AUDITORIA
# ============================================================

df_auditoria = consultar_auditoria(

    data_inicial,

    data_final,

    tecnico_filtro
)


# ============================================================
# RESULTADO DO HISTÓRICO
# ============================================================

if df_auditoria.empty:

    st.info(
        "Nenhuma transferência encontrada no período selecionado."
    )

else:

    # Formata data
    if "data_hora" in df_auditoria.columns:

        df_auditoria["data_hora"] = (
            pd.to_datetime(
                df_auditoria["data_hora"],
                errors="coerce"
            )
            .dt.strftime(
                "%d/%m/%Y %H:%M:%S"
            )
        )


    # ========================================================
    # COLUNAS PRINCIPAIS
    # ========================================================

    colunas_exibicao = [

        "id",

        "ticket_id",

        "cliente",

        "telefone",

        "tecnico_anterior",

        "tecnico_atual",

        "evento",

        "data_hora"
    ]


    colunas_exibicao = [

        coluna

        for coluna in colunas_exibicao

        if coluna in df_auditoria.columns
    ]


    df_exibicao = df_auditoria[
        colunas_exibicao
    ].copy()


    df_exibicao = df_exibicao.rename(
        columns={

            "id": "Registro",

            "ticket_id": "Ticket",

            "cliente": "Cliente",

            "telefone": "Telefone",

            "tecnico_anterior": "Técnico anterior",

            "tecnico_atual": "Técnico atual",

            "evento": "Evento",

            "data_hora": "Data/Hora"
        }
    )


    st.dataframe(
        df_exibicao,
        use_container_width=True,
        hide_index=True
    )


    # ========================================================
    # CSV
    # ========================================================

    st.download_button(

        label="📥 Baixar CSV da auditoria",

        data=gerar_csv(
            df_auditoria
        ),

        file_name=(
            "auditoria_transferencias_"
            f"{data_inicial.strftime('%Y-%m-%d')}_"
            f"{data_final.strftime('%Y-%m-%d')}.csv"
        ),

        mime="text/csv",

        use_container_width=True
    )


# ============================================================
# ADMINISTRAÇÃO DO ESTADO TEMPORÁRIO
# ============================================================

st.divider()


st.header(
    "🧹 Limpeza do estado temporário"
)


st.caption(
    "O estado temporário contém somente os atendimentos que "
    "o monitoramento usa para descobrir mudanças de técnico."
)


col_temp1, col_temp2 = st.columns(
    [1, 2]
)


with col_temp1:

    if os.path.exists(TEMP_STATE_FILE):

        estado_temp = carregar_estado_temporario()

        st.metric(
            "Registros temporários",
            len(estado_temp)
        )

    else:

        st.metric(
            "Registros temporários",
            0
        )


with col_temp2:

    st.write(
        "Selecione o período para limpar registros antigos."
    )


col_limpeza1, col_limpeza2 = st.columns(
    2
)


with col_limpeza1:

    data_limpeza_inicial = st.date_input(
        "Data inicial da limpeza",
        value=date.today(),
        key="data_limpeza_temp_inicio"
    )


with col_limpeza2:

    data_limpeza_final = st.date_input(
        "Data final da limpeza",
        value=date.today(),
        key="data_limpeza_temp_fim"
    )


if st.button(
    "🧹 Verificar registros temporários para limpeza",
    use_container_width=True
):

    resultado_atual = (
        st.session_state.ultimo_resultado
    )


    ids_abertos = resultado_atual.get(
        "ids_abertos",
        set()
    )


    resultado_limpeza = limpar_estado_temporario(

        data_limpeza_inicial,

        data_limpeza_final,

        ids_abertos,

        confirmar=False
    )


    if (
        resultado_limpeza["status"]
        == "CONFIRMACAO_NECESSARIA"
    ):

        st.warning(
            "Existem atendimentos do período que ainda "
            "estão abertos. Eles não serão apagados."
        )


        st.write(
            f"Registros que podem ser removidos: "
            f"{resultado_limpeza['removiveis']}"
        )


        st.write(
            f"Atendimentos ainda abertos protegidos: "
            f"{len(resultado_limpeza['abertos'])}"
        )


        st.session_state.confirmar_limpeza_temp = True


    else:

        st.success(
            f"Limpeza concluída. "
            f"{resultado_limpeza['removidos']} registros removidos."
        )


if st.session_state.get(
    "confirmar_limpeza_temp",
    False
):

    st.warning(
        "Confirme abaixo para excluir os registros "
        "temporários que não estão mais abertos."
    )


    col_conf1, col_conf2 = st.columns(
        2
    )


    with col_conf1:

        if st.button(
            "✅ Confirmar limpeza",
            type="primary",
            use_container_width=True
        ):

            resultado_atual = (
                st.session_state.ultimo_resultado
            )


            ids_abertos = resultado_atual.get(
                "ids_abertos",
                set()
            )


            resultado_limpeza = (
                limpar_estado_temporario(

                    data_limpeza_inicial,

                    data_limpeza_final,

                    ids_abertos,

                    confirmar=True
                )
            )


            st.session_state.confirmar_limpeza_temp = False


            st.success(
                f"{resultado_limpeza['removidos']} "
                f"registros temporários removidos."
            )


            st.rerun()


    with col_conf2:

        if st.button(
            "❌ Cancelar",
            use_container_width=True
        ):

            st.session_state.confirmar_limpeza_temp = False

            st.rerun()


# ============================================================
# ADMINISTRAÇÃO DA AUDITORIA
# ============================================================

st.divider()


st.header(
    "🗑️ Administração da auditoria"
)


st.caption(
    "Use esta opção somente se realmente quiser apagar "
    "registros do banco principal de auditoria."
)


col_del1, col_del2 = st.columns(
    2
)


with col_del1:

    data_exclusao_inicio = st.date_input(
        "Data inicial da exclusão",
        value=date.today(),
        key="data_exclusao_inicio"
    )


with col_del2:

    data_exclusao_fim = st.date_input(
        "Data final da exclusão",
        value=date.today(),
        key="data_exclusao_fim"
    )


if st.button(
    "🗑️ Excluir registros da auditoria neste período",
    use_container_width=True
):

    if data_exclusao_inicio > data_exclusao_fim:

        st.error(
            "A data inicial não pode ser maior que a data final."
        )

    else:

        st.session_state.confirmar_exclusao_auditoria = True


if st.session_state.get(
    "confirmar_exclusao_auditoria",
    False
):

    st.error(
        "ATENÇÃO: esta operação apagará permanentemente "
        "os registros da auditoria selecionados."
    )


    col_del_conf1, col_del_conf2 = st.columns(
        2
    )


    with col_del_conf1:

        if st.button(
            "⚠️ CONFIRMAR EXCLUSÃO",
            type="primary",
            use_container_width=True
        ):

            removidos = excluir_auditoria_periodo(

                data_exclusao_inicio,

                data_exclusao_fim
            )


            st.session_state.confirmar_exclusao_auditoria = False


            st.success(
                f"{removidos} registros de auditoria foram removidos."
            )


            st.rerun()


    with col_del_conf2:

        if st.button(
            "❌ Cancelar exclusão",
            use_container_width=True
        ):

            st.session_state.confirmar_exclusao_auditoria = False

            st.rerun()


# ============================================================
# INFORMAÇÕES DO SISTEMA
# ============================================================

st.divider()


with st.expander(
    "ℹ️ Como funciona a auditoria"
):

    st.write(
        """
        **1. Cliente entra em contato**

        O ticket aparece na fila monitorada.

        Exemplo:

        Ticket 10130 → Thiago

        Neste momento nada é gravado no banco de auditoria.


        **2. O sistema continua monitorando**

        Enquanto permanecer:

        Ticket 10130 → Thiago

        nenhuma nova informação é gravada.


        **3. O técnico muda**

        Se a API passar a informar:

        Ticket 10130 → Gabriel

        o sistema entende que houve alteração de responsável.


        **4. Somente nesse momento é criado o registro**

        O banco recebe:

        Ticket: 10130

        Cliente: 36202346487953

        Técnico anterior: Thiago

        Técnico atual: Gabriel

        Data/Hora: momento em que a alteração foi detectada.


        **5. O estado temporário é atualizado**

        Depois da transferência:

        Ticket 10130 → Gabriel

        Assim, se continuar com Gabriel, não haverá novos
        registros.


        **6. Se posteriormente mudar novamente**

        Exemplo:

        Gabriel → Ramon

        será criado outro registro:

        Técnico anterior: Gabriel

        Técnico atual: Ramon


        Dessa forma o banco principal contém somente eventos
        relevantes para auditoria.
        """
    )
