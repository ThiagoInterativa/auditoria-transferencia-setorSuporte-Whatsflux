import streamlit as st
import requests
import psycopg2
import psycopg2.extras
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

# Arquivo local legado (mantido para compatibilidade caso ainda exista)
AUDIT_DB_FILE = "auditoria.db"

# ============================================================
# BANCO PERSISTENTE (SUPABASE / POSTGRESQL)
# ============================================================

try:
    DATABASE_URL = st.secrets["DATABASE_URL"]
except Exception:
    DATABASE_URL = os.environ.get("DATABASE_URL", "")


# Fuso horário
TZ = ZoneInfo("America/Sao_Paulo")

# Intervalo padrão
DEFAULT_INTERVAL = 2.0


# ============================================================
# INICIALIZAÇÃO DE ESTADO DO STREAMLIT (SESSION STATE)
# ============================================================

if "monitorando" not in st.session_state:
    st.session_state.monitorando = False

if "whats_session" not in st.session_state:
    st.session_state.whats_session = None

if "login_status" not in st.session_state:
    st.session_state.login_status = None

if "total_ciclos" not in st.session_state:
    st.session_state.total_ciclos = 0

if "ultima_execucao" not in st.session_state:
    st.session_state.ultima_execucao = None

if "ultima_transferencias" not in st.session_state:
    st.session_state.ultima_transferencias = []

if "auditoria_selecionados" not in st.session_state:
    st.session_state.auditoria_selecionados = []

if "confirmar_exclusao_auditoria_selecionados" not in st.session_state:
    st.session_state.confirmar_exclusao_auditoria_selecionados = False

if "mostrar_exclusao_individual" not in st.session_state:
    st.session_state.mostrar_exclusao_individual = False


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
    /* Adicione as duas linhas abaixo para travar a altura e alinhar o conteúdo */
    min-height: 120px;
    display: flex;
    flex-direction: column;
    justify-content: center;
    
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


/* ========================================================
   AUDITORIA
   ======================================================== */

.audit-header {
    margin-top: 8px;
    margin-bottom: 8px;
}

.audit-actions {
    margin-top: 8px;
    margin-bottom: 8px;
}

.audit-confirmation {
    background: #fff7ed;
    border: 1px solid #fdba74;
    border-radius: 8px;
    padding: 12px 16px;
    margin-top: 8px;
    margin-bottom: 8px;
}

.audit-info {
    color: #64748b;
    font-size: 13px;
    margin-bottom: 8px;
}

/* ============================================================
   AVISOS DE CONFIRMAÇÃO DE EXCLUSÃO
   ============================================================ */

.confirmacao-exclusao {
    background: #fff7ed;
    border: 1px solid #fdba74;
    border-radius: 8px;
    padding: 14px 16px;
    margin: 10px 0 14px 0;
    color: #000000 !important;
}

.confirmacao-exclusao * {
    color: #000000 !important;
}

.confirmacao-exclusao-titulo {
    font-size: 16px;
    font-weight: 700;
    color: #000000 !important;
    margin-bottom: 6px;
}

.confirmacao-exclusao-texto {
    font-size: 14px;
    color: #000000 !important;
    line-height: 1.5;
}

.exclusao-individual {
    background: #f8fafc;
    border: 1px solid #cbd5e1;
    border-radius: 8px;
    padding: 12px 16px;
    margin-top: 10px;
    margin-bottom: 10px;
    color: #000000 !important;
}

.exclusao-individual * {
    color: #000000 !important;
}

.exclusao-individual-titulo {
    font-size: 15px;
    font-weight: 700;
    color: #000000 !important;
    margin-bottom: 5px;
}

.exclusao-individual-texto {
    font-size: 13px;
    color: #000000 !important;
    line-height: 1.4;
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
        if isinstance(valor, datetime):
            dt = valor
        else:
            dt = datetime.fromisoformat(str(valor))

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
# CONEXÃO COM POSTGRESQL (SUPABASE)
# ============================================================

def conectar_banco():
    """
    Abre uma conexão com o PostgreSQL persistente no Supabase.
    O banco não fica dentro do container do Streamlit Cloud.
    Portanto, restart/redeploy da aplicação não apaga os registros.
    """
    if not DATABASE_URL:
        raise ValueError(
            "DATABASE_URL não está configurada nos Secrets do Streamlit! "
            "Adicione DATABASE_URL = 'postgresql://...' em .streamlit/secrets.toml"
        )

    # Garante protocolo compatível com psycopg2 (troca postgres:// por postgresql:// se necessário)
    url = DATABASE_URL
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    conn = psycopg2.connect(
        url,
        connect_timeout=15,
        sslmode="require"
    )

    return conn


# ============================================================
# INICIALIZAÇÃO DO BANCO (CRIAÇÃO DAS TABELAS)
# ============================================================

def inicializar_banco():
    """
    Garante que as tabelas persistentes existam no PostgreSQL (Supabase).
    Mesmo que a aplicação reinicie no Streamlit Cloud, os dados continuam intactos.
    """
    conn = conectar_banco()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS auditoria (
                    id BIGSERIAL PRIMARY KEY,
                    ticket_id BIGINT NOT NULL,
                    ticket_uuid TEXT,
                    contact_id BIGINT,
                    cliente TEXT,
                    telefone TEXT,
                    tecnico_anterior_id BIGINT,
                    tecnico_anterior TEXT,
                    tecnico_atual_id BIGINT,
                    tecnico_atual TEXT,
                    evento TEXT NOT NULL,
                    data_hora TIMESTAMPTZ NOT NULL
                );
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_auditoria_ticket
                ON auditoria(ticket_id);
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_auditoria_data
                ON auditoria(data_hora);
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS monitor_estado (
                    ticket_id TEXT PRIMARY KEY,
                    ticket_uuid TEXT,
                    contact_id BIGINT,
                    cliente TEXT,
                    telefone TEXT,
                    tecnico_id BIGINT,
                    tecnico TEXT,
                    detectado_em TIMESTAMPTZ NOT NULL
                );
            """)

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ============================================================
# CONSULTAS DE MÉTRICAS DA AUDITORIA
# ============================================================

def contar_transferencias_hoje():
    """
    Busca no PostgreSQL apenas as transferências que ocorreram 
    no dia de hoje (entre 00:00:00 e 23:59:59).
    """
    conn = conectar_banco()
    try:
        with conn.cursor() as cursor:
            # Pegamos a data atual no fuso horário correto
            hoje_dt = agora().date()
            
            # Utilizamos as funções utilitárias que você já possui no código
            inicio = data_inicio(hoje_dt)
            fim = data_fim(hoje_dt)
            
            # Query filtrando pelo intervalo do dia de hoje
            query = """
                SELECT COUNT(*) 
                FROM auditoria 
                WHERE data_hora >= %s AND data_hora <= %s;
            """
            cursor.execute(query, (inicio, fim))
            resultado = cursor.fetchone()
            
            # Retorna o número de linhas encontradas
            return resultado[0] if resultado else 0
    except Exception as e:
        st.error(f"Erro ao contar transferências de hoje: {e}")
        return 0
    finally:
        conn.close()
        
# ============================================================
# ESTADO PERSISTENTE DO MONITORAMENTO (POSTGRESQL)
# ============================================================

def carregar_estado_temporario():
    """
    Carrega o estado monitorado diretamente do PostgreSQL (Supabase).
    Isso permite que o sistema continue sabendo quem era o técnico anterior
    mesmo após reinicialização ou novo deploy no Streamlit Cloud.
    """
    conn = conectar_banco()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute("""
                SELECT
                    ticket_id,
                    ticket_uuid,
                    contact_id,
                    cliente,
                    telefone,
                    tecnico_id,
                    tecnico,
                    detectado_em
                FROM monitor_estado
            """)

            registros = cursor.fetchall()
            estado = {}

            for registro in registros:
                item = dict(registro)
                valor = item.get("detectado_em")
                if hasattr(valor, "isoformat"):
                    item["detectado_em"] = valor.isoformat()
                estado[str(item["ticket_id"])] = item

            return estado
    finally:
        conn.close()


def salvar_estado_temporario(estado):
    """
    Salva o estado atual de monitoramento no PostgreSQL (Supabase).
    Utiliza transação atômica e UPSERT (ON CONFLICT DO UPDATE).
    """
    conn = conectar_banco()
    try:
        with conn.cursor() as cursor:
            chaves = list(estado.keys())
            if chaves:
                # Remove do banco os tickets que já foram finalizados (não constam mais no estado)
                cursor.execute(
                    "DELETE FROM monitor_estado WHERE ticket_id NOT IN %s",
                    (tuple(chaves),)
                )

                # Upsert dos atendimentos ativos
                sql_upsert = """
                    INSERT INTO monitor_estado (
                        ticket_id,
                        ticket_uuid,
                        contact_id,
                        cliente,
                        telefone,
                        tecnico_id,
                        tecnico,
                        detectado_em
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (ticket_id) DO UPDATE SET
                        ticket_uuid = EXCLUDED.ticket_uuid,
                        contact_id = EXCLUDED.contact_id,
                        cliente = EXCLUDED.cliente,
                        telefone = EXCLUDED.telefone,
                        tecnico_id = EXCLUDED.tecnico_id,
                        tecnico = EXCLUDED.tecnico,
                        detectado_em = EXCLUDED.detectado_em;
                """

                dados = [
                    (
                        str(item.get("ticket_id")),
                        item.get("ticket_uuid"),
                        int(item.get("contact_id")) if item.get("contact_id") is not None else None,
                        item.get("cliente"),
                        item.get("telefone"),
                        int(item.get("tecnico_id")) if item.get("tecnico_id") is not None else None,
                        item.get("tecnico"),
                        item.get("detectado_em")
                    )
                    for item in estado.values()
                ]

                psycopg2.extras.execute_batch(cursor, sql_upsert, dados)
            else:
                cursor.execute("DELETE FROM monitor_estado")

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ============================================================
# CONSULTAR ÚLTIMO EVENTO DO TICKET
# ============================================================

def buscar_ultimo_evento(ticket_id):
    conn = conectar_banco()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute("""
                SELECT *
                FROM auditoria
                WHERE ticket_id = %s
                ORDER BY id DESC
                LIMIT 1
            """, (ticket_id,))
            row = cursor.fetchone()
            if row:
                return dict(row)
            return None
    finally:
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
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
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
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                agora()
            ))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
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
        if page_number > 100:
            break

    return todos


# ============================================================
# PROCESSAMENTO DO TICKET
# ============================================================

def processar_ticket(ticket, estado):
    ticket_id = ticket.get("id")
    if not ticket_id:
        return None

    user = ticket.get("user")
    user_id = ticket.get("userId")

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

    gravar_transferencia(
        ticket=ticket,
        tecnico_anterior_id=tecnico_anterior_id,
        tecnico_anterior=tecnico_anterior,
        tecnico_atual_id=user_id,
        tecnico_atual=tecnico_atual
    )

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
    ids_abertos = set()

    for ticket in tickets:
        ticket_id = ticket.get("id")
        if ticket_id:
            ids_abertos.add(str(ticket_id))

    for ticket in tickets:
        resultado = processar_ticket(ticket, estado)
        if not resultado:
            continue

        if resultado["tipo"] == "ENTRADA_MONITORAMENTO":
            entradas += 1
        elif resultado["tipo"] == "TRANSFERENCIA":
            transferencias.append(resultado)

    chaves_para_remover = []
    for chave_ticket in list(estado.keys()):
        if chave_ticket not in ids_abertos:
            chaves_para_remover.append(chave_ticket)

    for chave_ticket in chaves_para_remover:
        estado.pop(chave_ticket, None)

    salvar_estado_temporario(estado)

    return {
        "tickets_abertos": len(tickets),
        "ids_abertos": ids_abertos,
        "estado": estado,
        "entradas": entradas,
        "transferencias": transferencias
    }


# ============================================================
# AUDITORIA - CONSULTA (POSTGRESQL)
# ============================================================

def consultar_auditoria(data_inicial, data_final, tecnico=None):
    inicio = data_inicio(data_inicial)
    fim = data_fim(data_final)

    conn = conectar_banco()
    try:
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
            WHERE data_hora >= %s
            AND data_hora <= %s
        """

        params = [inicio, fim]

        if tecnico and tecnico != "Todos":
            sql += """
                AND (
                    tecnico_anterior = %s
                    OR tecnico_atual = %s
                )
            """
            params.extend([tecnico, tecnico])

        sql += " ORDER BY data_hora DESC"

        df = pd.read_sql_query(sql, conn, params=params)
        return df
    finally:
        conn.close()


# ============================================================
# TÉCNICOS EXISTENTES NA AUDITORIA
# ============================================================

def listar_tecnicos_auditoria():
    conn = conectar_banco()
    try:
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

        if df.empty:
            return []

        return df["tecnico"].dropna().tolist()
    finally:
        conn.close()


# ============================================================
# ESTATÍSTICAS
# ============================================================

def quantidade_auditoria():
    conn = conectar_banco()
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM auditoria")
            row = cursor.fetchone()
            return row[0] if row else 0
    finally:
        conn.close()


# ============================================================
# ESTADO TEMPORÁRIO - DATA
# ============================================================

def data_estado(item):
    valor = item.get("detectado_em")
    try:
        if isinstance(valor, datetime):
            dt = valor
        else:
            dt = datetime.fromisoformat(str(valor))

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

        if not (inicio <= dt <= fim):
            continue

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

    if abertos_no_periodo and not confirmar:
        return {
            "status": "CONFIRMACAO_NECESSARIA",
            "removiveis": len(candidatos),
            "abertos": abertos_no_periodo
        }

    for chave in candidatos:
        estado.pop(chave, None)

    salvar_estado_temporario(estado)

    return {
        "status": "OK",
        "removidos": len(candidatos),
        "abertos": len(abertos_no_periodo)
    }


# ============================================================
# EXCLUIR AUDITORIA - PERÍODO
# ============================================================

def excluir_auditoria_periodo(data_inicial, data_final):
    inicio = data_inicio(data_inicial)
    fim = data_fim(data_final)

    conn = conectar_banco()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                DELETE FROM auditoria
                WHERE data_hora >= %s
                AND data_hora <= %s
            """, (inicio, fim))
            removidos = cursor.rowcount
        conn.commit()
        return removidos
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ============================================================
# EXCLUIR UM REGISTRO ESPECÍFICO
# ============================================================

def excluir_auditoria_registro(registro_id):
    conn = conectar_banco()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                DELETE FROM auditoria
                WHERE id = %s
            """, (registro_id,))
            removido = cursor.rowcount
        conn.commit()
        return removido
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ============================================================
# EXCLUIR VÁRIOS REGISTROS ESPECÍFICOS
# ============================================================

def excluir_auditoria_registros(registro_ids):
    if not registro_ids:
        return 0

    conn = conectar_banco()
    try:
        with conn.cursor() as cursor:
            cursor.execute("""
                DELETE FROM auditoria
                WHERE id = ANY(%s)
            """, (list(registro_ids),))
            removidos = cursor.rowcount
        conn.commit()
        return removidos
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ============================================================
# GERAR CSV DA AUDITORIA
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
        df = pd.DataFrame(columns=colunas)
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

    return buffer.getvalue().encode("utf-8-sig")


# ============================================================
# GERAR BACKUP COMPLETO DO BANCO (SQL DUMP SUPABASE)
# ============================================================

def gerar_backup_sql():
    """
    Gera um backup completo em script SQL com todas as transferências
    auditadas no Supabase, pronto para restauração.
    """
    conn = conectar_banco()
    try:
        df_todas = pd.read_sql_query(
            "SELECT * FROM auditoria ORDER BY id ASC",
            conn
        )
    finally:
        conn.close()

    linhas_sql = [
        "-- Backup do Banco de Auditoria WhatsFlux (PostgreSQL/Supabase)",
        f"-- Data de extração: {agora_iso()}",
        "-- Total de registros: " + str(len(df_todas)),
        "",
        "CREATE TABLE IF NOT EXISTS auditoria (",
        "    id BIGSERIAL PRIMARY KEY,",
        "    ticket_id BIGINT NOT NULL,",
        "    ticket_uuid TEXT,",
        "    contact_id BIGINT,",
        "    cliente TEXT,",
        "    telefone TEXT,",
        "    tecnico_anterior_id BIGINT,",
        "    tecnico_anterior TEXT,",
        "    tecnico_atual_id BIGINT,",
        "    tecnico_atual TEXT,",
        "    evento TEXT NOT NULL,",
        "    data_hora TIMESTAMPTZ NOT NULL",
        ");",
        ""
    ]

    for _, row in df_todas.iterrows():
        t_uuid = f"'{row['ticket_uuid']}'" if pd.notna(row['ticket_uuid']) else "NULL"
        c_id = str(int(row['contact_id'])) if pd.notna(row['contact_id']) else "NULL"
        cliente_clean = str(row['cliente']).replace("'", "''") if pd.notna(row['cliente']) else None
        cli = f"'{cliente_clean}'" if cliente_clean is not None else "NULL"
        tel = f"'{row['telefone']}'" if pd.notna(row['telefone']) else "NULL"
        ant_id = str(int(row['tecnico_anterior_id'])) if pd.notna(row['tecnico_anterior_id']) else "NULL"
        ant_clean = str(row['tecnico_anterior']).replace("'", "''") if pd.notna(row['tecnico_anterior']) else None
        ant = f"'{ant_clean}'" if ant_clean is not None else "NULL"
        atu_id = str(int(row['tecnico_atual_id'])) if pd.notna(row['tecnico_atual_id']) else "NULL"
        atu_clean = str(row['tecnico_atual']).replace("'", "''") if pd.notna(row['tecnico_atual']) else None
        atu = f"'{atu_clean}'" if atu_clean is not None else "NULL"
        evt = f"'{row['evento']}'"
        dh = f"'{row['data_hora']}'"

        linhas_sql.append(
            f"INSERT INTO auditoria (id, ticket_id, ticket_uuid, contact_id, cliente, telefone, "
            f"tecnico_anterior_id, tecnico_anterior, tecnico_atual_id, tecnico_atual, evento, data_hora) "
            f"VALUES ({row['id']}, {row['ticket_id']}, {t_uuid}, {c_id}, {cli}, {tel}, "
            f"{ant_id}, {ant}, {atu_id}, {atu}, {evt}, {dh}) "
            f"ON CONFLICT (id) DO NOTHING;"
        )

    return "\n".join(linhas_sql).encode("utf-8")


# ============================================================
# INICIALIZAÇÃO AUTOMÁTICA DAS TABELAS NO SUPABASE
# ============================================================

try:
    inicializar_banco()
except Exception as e:
    st.error(
        f"⚠️ Erro ao conectar ao banco de dados Supabase: {e}\n\n"
        "Verifique se `DATABASE_URL` está configurada corretamente nos Secrets do Streamlit Cloud."
    )


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
    st.sidebar.success(
        "🟢 Monitoramento ativo"
    )
else:
    st.sidebar.warning(
        "⏸️ Monitoramento pausado"
    )

st.sidebar.divider()

st.sidebar.subheader("🧹 Limpeza")

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

st.markdown(
    '<div class="main-title">'
    '🔎 Auditoria de Transferências WhatsFlux'
    '</div>',
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
        resultado = executar_monitoramento(session)

        st.session_state.total_ciclos += 1
        st.session_state.ultima_execucao = agora()
        st.session_state.ultima_transferencias = (
            resultado.get("transferencias", [])
        )

        transferencias = resultado.get("transferencias", [])

        if transferencias:
            for transferencia in transferencias:
                st.toast(
                    (
                        f"🔄 Transferência detectada: "
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
    # INDICADORES
    # ========================================================
    # 1. Buscamos os valores atualizados direto do banco de dados
    total_hoje = contar_transferencias_hoje()
    estado = resultado.get("estado", {})
    tickets_abertos = resultado.get("tickets_abertos", 0)
    entradas = resultado.get("entradas", 0)
    transferencias = resultado.get("transferencias", [])

    col1, col2, col3, col4, col5 = st.columns(5)

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
        # CORRIGIDO: O comentário agora está alinhado corretamente com 8 espaços
        # Card original modificado: Transferência auditada (Mostra APENAS o dia de hoje)
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-number">
                    {total_hoje}
                </div>
                <div class="metric-label">
                    Transferência (Hoje)
                </div>
            </div>
            """,
            unsafe_allow_html=True
        )
    
    with col5:
        st.markdown(
            f"""
            <div class="metric-card">
                <div class="metric-number">
                    {quantidade_auditoria()}
                </div>
                <div class="metric-label">
                    Transferências (periodo)
                </div>
            </div>
            """,
            unsafe_allow_html=True
        )

    st.write("")

    # ========================================================
    # STATUS DO MONITOR
    # ========================================================
    ultima = st.session_state.ultima_execucao

    if ultima:
        horario = ultima.strftime("%d/%m/%Y %H:%M:%S")
    else:
        horario = "-"

    st.markdown(
        f"""
        <div class="small-info">
            🟢 Monitoramento ativo |
            Última consulta:
            <strong>{horario}</strong> |
            Ciclos executados:
            <strong>{st.session_state.total_ciclos}</strong>
        </div>
        """,
        unsafe_allow_html=True
    )

    # ========================================================
    # TRANSFERÊNCIAS DETECTADAS
    # ========================================================
    if transferencias:
        st.write("")
        st.subheader("🔄 Transferências detectadas neste ciclo")

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
        st.info("Nenhuma transferência detectada neste ciclo.")

    # ========================================================
    # ABAS
    # ========================================================
    aba_monitoramento, aba_auditoria = st.tabs([
        "👥 Atendimentos monitorados",
        "🔎 Visualizar auditoria"
    ])

    # ========================================================
    # ABA 1
    # ========================================================
    with aba_monitoramento:
        if estado:
            linhas = []
            for item in estado.values():
                linhas.append({
                    "Ticket": item.get("ticket_id"),
                    "Cliente": item.get("cliente"),
                    "Telefone": item.get("telefone"),
                    "Técnico": item.get("tecnico"),
                    "Detectado em": formatar_data_hora(item.get("detectado_em"))
                })

            df_estado = pd.DataFrame(linhas)

            if not df_estado.empty:
                st.dataframe(
                    df_estado,
                    use_container_width=True,
                    hide_index=True
                )
        else:
            st.info(
                "Nenhum atendimento com técnico está sendo monitorado."
            )

    # ========================================================
    # ABA 2 - VISUALIZAR AUDITORIA
    # ========================================================
    with aba_auditoria:
        st.markdown(
            '<div class="audit-header">'
            '<h3>🔎 Visualizar auditoria</h3>'
            '</div>',
            unsafe_allow_html=True
        )

        col_auditoria_data1, col_auditoria_data2 = st.columns(2)

        with col_auditoria_data1:
            data_auditoria_inicial = st.date_input(
                "Data inicial",
                value=date.today() - timedelta(days=30),
                key="data_auditoria_inicial"
            )

        with col_auditoria_data2:
            data_auditoria_final = st.date_input(
                "Data final",
                value=date.today(),
                key="data_auditoria_final"
            )

        if data_auditoria_inicial > data_auditoria_final:
            st.error(
                "❌ A data inicial não pode ser maior que a data final."
            )
        else:
            df_auditoria = consultar_auditoria(
                data_inicial=data_auditoria_inicial,
                data_final=data_auditoria_final,
                tecnico="Todos"
            )

            if df_auditoria.empty:
                st.session_state.auditoria_selecionados = []
                st.info(
                    "ℹ️ Nenhum registro de auditoria encontrado no período selecionado."
                )
            else:
                st.markdown(
                    f"""
                    <div class="audit-info">
                        {len(df_auditoria)} registro(s) encontrado(s).
                        Selecione os registros diretamente na tabela para excluir.
                    </div>
                    """,
                    unsafe_allow_html=True
                )

                df_tabela = pd.DataFrame({
                    "id": df_auditoria["id"],
                    "Ticket": "#" + df_auditoria["ticket_id"].astype(str),
                    "Cliente": df_auditoria["cliente"].fillna(""),
                    "Anterior": df_auditoria["tecnico_anterior"].fillna("-"),
                    "Atual": df_auditoria["tecnico_atual"].fillna("-"),
                    "Data/Hora": df_auditoria["data_hora"].apply(formatar_data_hora)
                })

                df_tabela["Selecionar"] = False

                df_tabela = df_tabela[
                    [
                        "Selecionar",
                        "Ticket",
                        "Cliente",
                        "Anterior",
                        "Atual",
                        "Data/Hora",
                        "id"
                    ]
                ]

                configuracao_colunas = {
                    "Selecionar": st.column_config.CheckboxColumn(
                        "✓",
                        help="Marque para selecionar o registro.",
                        default=False
                    ),
                    "Ticket": st.column_config.TextColumn(
                        "Ticket",
                        width="small"
                    ),
                    "Cliente": st.column_config.TextColumn(
                        "Cliente",
                        width="medium"
                    ),
                    "Anterior": st.column_config.TextColumn(
                        "Anterior",
                        width="medium"
                    ),
                    "Atual": st.column_config.TextColumn(
                        "Atual",
                        width="medium"
                    ),
                    "Data/Hora": st.column_config.TextColumn(
                        "Data/Hora",
                        width="medium"
                    ),
                    "id": None
                }

                tabela_editada = st.data_editor(
                    df_tabela,
                    column_config=configuracao_colunas,
                    column_order=[
                        "Selecionar",
                        "Ticket",
                        "Cliente",
                        "Anterior",
                        "Atual",
                        "Data/Hora"
                    ],
                    hide_index=True,
                    use_container_width=True,
                    height=min(430, 45 + (len(df_tabela) * 35)),
                    disabled=[
                        "Ticket",
                        "Cliente",
                        "Anterior",
                        "Atual",
                        "Data/Hora",
                        "id"
                    ],
                    key="tabela_auditoria"
                )

                selecionados = tabela_editada[
                    tabela_editada["Selecionar"] == True
                ]

                ids_selecionados = (
                    selecionados["id"]
                    .astype(int)
                    .tolist()
                )

                st.session_state.auditoria_selecionados = ids_selecionados

                st.markdown(
                    '<div class="audit-actions"></div>',
                    unsafe_allow_html=True
                )

                quantidade_selecionada = len(ids_selecionados)

                col_acao1, col_acao2, col_acao3 = st.columns([1.5, 1.5, 5])

                with col_acao1:
                    if quantidade_selecionada > 0:
                        if st.button(
                            f"🗑️ Excluir selecionados ({quantidade_selecionada})",
                            use_container_width=True,
                            key="btn_excluir_selecionados"
                        ):
                            st.session_state.confirmar_exclusao_auditoria_selecionados = True
                            st.rerun()
                    else:
                        st.button(
                            "🗑️ Excluir selecionados",
                            disabled=True,
                            use_container_width=True,
                            key="btn_excluir_selecionados_disabled"
                        )

                with col_acao2:
                    if st.button(
                        "🗑️ Excluir 1 registro",
                        disabled=(len(df_auditoria) == 0),
                        use_container_width=True,
                        key="btn_exclusao_individual"
                    ):
                        st.session_state.mostrar_exclusao_individual = True
                        st.rerun()

                with col_acao3:
                    if quantidade_selecionada > 0:
                        st.caption(
                            f"{quantidade_selecionada} registro(s) selecionado(s)."
                        )
                    else:
                        st.caption(
                            "Você pode selecionar um ou vários registros na primeira coluna."
                        )

                # Confirmação - exclusão múltipla
                if st.session_state.get(
                    "confirmar_exclusao_auditoria_selecionados",
                    False
                ):
                    st.markdown(
                        """
                        <div class="confirmacao-exclusao">
                            <div class="confirmacao-exclusao-titulo">
                                ⚠️ Confirmar exclusão
                            </div>
                            <div class="confirmacao-exclusao-texto">
                                Os registros selecionados serão removidos definitivamente
                                da auditoria no Supabase. Essa ação não poderá ser desfeita.
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True
                    )

                    col_conf1, col_conf2 = st.columns(2)

                    with col_conf1:
                        if st.button(
                            "✅ Sim, excluir selecionados",
                            type="primary",
                            use_container_width=True,
                            key="confirmar_exclusao_multiplos"
                        ):
                            ids_para_excluir = st.session_state.auditoria_selecionados
                            removidos = excluir_auditoria_registros(ids_para_excluir)

                            st.session_state.auditoria_selecionados = []
                            st.session_state.confirmar_exclusao_auditoria_selecionados = False

                            st.toast(
                                f"{removidos} registro(s) excluído(s).",
                                icon="🗑️"
                            )
                            st.rerun()

                    with col_conf2:
                        if st.button(
                            "❌ Cancelar",
                            use_container_width=True,
                            key="cancelar_exclusao_multiplos"
                        ):
                            st.session_state.auditoria_selecionados = []
                            st.session_state.confirmar_exclusao_auditoria_selecionados = False
                            st.rerun()

                # Confirmação - exclusão individual
                if st.session_state.get(
                    "mostrar_exclusao_individual",
                    False
                ):
                    st.markdown(
                        """
                        <div class="exclusao-individual">
                            <div class="exclusao-individual-titulo">
                                🗑️ Exclusão individual
                            </div>
                            <div class="exclusao-individual-texto">
                                Selecione abaixo o registro que deseja excluir do Supabase.
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True
                    )

                    opcoes_individuais = {}
                    for _, registro in df_auditoria.iterrows():
                        registro_id = int(registro["id"])
                        descricao = (
                            f"Ticket #{registro['ticket_id']} "
                            f"| {registro['cliente'] or '-'} "
                            f"| {registro['tecnico_anterior'] or '-'} "
                            f"→ {registro['tecnico_atual'] or '-'} "
                            f"| {formatar_data_hora(registro['data_hora'])}"
                        )
                        opcoes_individuais[descricao] = registro_id

                    opcao_escolhida = st.selectbox(
                        "Registro:",
                        options=list(opcoes_individuais.keys()),
                        key="registro_individual_escolhido"
                    )

                    col_ind1, col_ind2 = st.columns(2)

                    with col_ind1:
                        if st.button(
                            "🗑️ Excluir este registro",
                            type="primary",
                            use_container_width=True,
                            key="confirmar_individual"
                        ):
                            reg_id = opcoes_individuais[opcao_escolhida]
                            removido = excluir_auditoria_registro(reg_id)
                            st.session_state.mostrar_exclusao_individual = False

                            if removido:
                                st.toast("Registro excluído do Supabase.", icon="🗑️")
                            else:
                                st.error("Não foi possível excluir o registro.")

                            st.rerun()

                    with col_ind2:
                        if st.button(
                            "❌ Cancelar",
                            use_container_width=True,
                            key="cancelar_individual"
                        ):
                            st.session_state.mostrar_exclusao_individual = False
                            st.rerun()

        # ========================================================
        # EXPORTAÇÃO E DOWNLOAD DA AUDITORIA
        # ========================================================
        st.write("")
        st.subheader("📦 Exportar auditoria")
        st.caption(
            "Exporte os registros da auditoria em CSV "
            "ou baixe um backup SQL completo do Supabase para análise."
        )

        col_csv1, col_csv2 = st.columns(2)

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

        if data_csv_inicial > data_csv_final:
            st.error("❌ A data inicial não pode ser maior que a data final.")
        else:
            df_csv = consultar_auditoria(
                data_inicial=data_csv_inicial,
                data_final=data_csv_final,
                tecnico="Todos"
            )

            arquivo_csv = gerar_csv(df_csv)

            nome_arquivo = (
                f"auditoria_transferencias_"
                f"{data_csv_inicial.strftime('%Y%m%d')}_"
                f"{data_csv_final.strftime('%Y%m%d')}.csv"
            )

            col_exportar_csv, col_baixar_banco = st.columns(2)

            with col_exportar_csv:
                st.markdown(
                    """
                    <div style="font-weight: 700; font-size: 16px; margin-bottom: 6px;">
                        📄 Exportar CSV
                    </div>
                    """,
                    unsafe_allow_html=True
                )

                if df_csv.empty:
                    st.caption("Nenhuma transferência encontrada no período. Será gerado vazio.")
                else:
                    st.caption(f"{len(df_csv)} transferência(s) encontrada(s) no período.")

                st.download_button(
                    label="📥 Baixar CSV da auditoria",
                    data=arquivo_csv,
                    file_name=nome_arquivo,
                    mime="text/csv",
                    use_container_width=True,
                    key="download_csv_auditoria"
                )

            with col_baixar_banco:
                st.markdown(
                    """
                    <div style="font-weight: 700; font-size: 16px; margin-bottom: 6px;">
                        🗄️ Baixar banco de auditoria
                    </div>
                    """,
                    unsafe_allow_html=True
                )

                # Oferece exportação SQL persistente do PostgreSQL / Supabase
                try:
                    backup_sql = gerar_backup_sql()
                    qtd_registros = quantidade_auditoria()
                    st.caption(f"Banco Supabase PostgreSQL ({qtd_registros} registros auditados)")

                    st.download_button(
                        label="📥 Baixar backup_auditoria.sql",
                        data=backup_sql,
                        file_name=f"auditoria_supabase_{date.today().strftime('%Y%m%d')}.sql",
                        mime="application/sql",
                        use_container_width=True,
                        key="download_backup_sql"
                    )
                except Exception as e:
                    # Fallback para o arquivo SQLite antigo se existir
                    if os.path.exists(AUDIT_DB_FILE):
                        with open(AUDIT_DB_FILE, "rb") as arquivo_db:
                            banco_bytes = arquivo_db.read()
                        st.download_button(
                            label="📥 Baixar auditoria.db (Legado)",
                            data=banco_bytes,
                            file_name="auditoria.db",
                            mime="application/vnd.sqlite3",
                            use_container_width=True,
                            key="download_banco_auditoria_fallback"
                        )
                    else:
                        st.warning(f"⚠️ Não foi possível gerar o backup SQL: {e}")


# ============================================================
# EXECUTA O MONITORAMENTO
# ============================================================

painel_monitoramento()
