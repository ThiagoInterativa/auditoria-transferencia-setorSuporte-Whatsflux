import io
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
import pandas as pd
import streamlit as st

# ============================================================
# CONFIGURAÇÕES E CONSTANTES
# ============================================================

DEFAULT_INTERVAL = 2.0
QUEUE_ID = os.getenv("WHATSFLUX_QUEUE_ID", "default_queue")
TZ = timezone(timedelta(hours=-3))  # Ajuste conforme seu fuso horário (ex: Brasília)

# Configuração da página do Streamlit deve ser a primeira chamada Streamlit
st.set_page_config(
    page_title="Auditoria de Transferências WhatsFlux",
    page_icon="🔎",
    layout="wide"
)

# ============================================================
# FUNÇÕES DE SUPORTE / MOCK (AJUSTE CONFORME SEU AMBIENTE)
# ============================================================

def conectar_banco():
    return sqlite3.connect("auditoria.db", check_same_thread=False)

def inicializar_banco():
    conn = conectar_banco()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id TEXT,
            ticket_uuid TEXT,
            contact_id TEXT,
            cliente TEXT,
            telefone TEXT,
            tecnico_anterior_id TEXT,
            tecnico_anterior TEXT,
            tecnico_atual_id TEXT,
            tecnico_atual TEXT,
            evento TEXT,
            data_hora TEXT
        )
    """)
    conn.commit()
    conn.close()

def quantidade_auditoria():
    try:
        conn = conectar_banco()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM auditoria")
        count = cursor.fetchone()[0]
        conn.close()
        return count
    except Exception:
        return 0

def consultar_auditoria(data_inicial, data_final, tecnico="Todos"):
    inicio = data_inicio(data_inicial).isoformat()
    fim = data_fim(data_final).isoformat()
    conn = conectar_banco()
    query = "SELECT * FROM auditoria WHERE data_hora >= ? AND data_hora <= ?"
    params = [inicio, fim]
    if tecnico and tecnico != "Todos":
        query += " AND (tecnico_anterior = ? OR tecnico_atual = ?)"
        params.extend([tecnico, tecnico])
    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return df

def data_inicio(d):
    return datetime.combine(d, datetime.min.time(), tzinfo=TZ)

def data_fim(d):
    return datetime.combine(d, datetime.max.time(), tzinfo=TZ)

def formatar_data_hora(valor):
    if not valor:
        return "-"
    try:
        if isinstance(valor, str):
            dt = datetime.fromisoformat(valor)
        else:
            dt = valor
        return dt.strftime("%d/%m/%Y %H:%M:%S")
    except Exception:
        return str(valor)

def agora():
    return datetime.now(TZ)

def criar_sessao_whatsflux():
    # Função simulada de autenticação
    return "session_token_mock", ""

def carregar_estado_temporario():
    return {}

def salvar_estado_temporario(estado):
    pass

def buscar_tickets_abertos(session):
    return []

def executar_monitoramento(session):
    return {
        "transferencias": [],
        "estado": {},
        "tickets_abertos": 0,
        "entradas": 0
    }

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
# EXCLUIR UM REGISTRO ESPECÍFICO DA AUDITORIA
# ============================================================

def excluir_auditoria_registro(registro_id):
    conn = conectar_banco()
    cursor = conn.execute("""
        DELETE FROM auditoria
        WHERE id = ?
    """, (
        registro_id,
    ))

    removido = cursor.rowcount
    conn.commit()
    conn.close()

    return removido


# ============================================================
# GERAR CSV DA AUDITORIA
# ============================================================

def gerar_csv(df):
    """
    Gera o arquivo CSV da auditoria.

    IMPORTANTE:
    Mesmo que o DataFrame esteja vazio, o CSV será criado
    contendo os nomes das colunas.

     Isso permite testar o formato do relatório antes de
    existirem transferências registradas.
    """
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
    "▶️️ Iniciar monitoramento",
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
    estado_atual = carregar_estado_temporario()
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

    estado = resultado.get("estado", {})

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

    col1, col2, col3, col4 = st.columns(4)

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
    # STATUS DO MONITOR
    # ========================================================

    ultima = st.session_state.ultima_execucao

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
            Última consulta: <strong>{horario}</strong> |
            Ciclos executados: <strong>{st.session_state.total_ciclos}</strong>
        </div>
        """,
        unsafe_allow_html=True
    )


    # ========================================================
    # TRANSFERÊNCIAS DETECTADAS NESTE CICLO
    # ========================================================

    if transferencias:
        st.write("")
        st.subheader(
            "🔄 Transferências detectadas neste ciclo"
        )

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
    # ABAS
    # ========================================================

    aba_monitoramento, aba_auditoria = st.tabs([
        "👥 Atendimentos monitorados",
        "🔎 Visualizar auditoria"
    ])


    # ========================================================
    # ABA 1 - ATENDIMENTOS ATUALMENTE MONITORADOS
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
                    "Detectado em": formatar_data_hora(
                        item.get("detectado_em")
                    )
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
        st.subheader("🔎 Visualizar auditoria")

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
                st.info(
                    "ℹ️ Nenhum registro de auditoria encontrado "
                    "no período selecionado."
                )
            else:
                st.success(
                    f"✅ {len(df_auditoria)} registro(s) "
                    f"encontrado(s)."
                )

                col1, col2, col3, col4, col5, col6, col7, col8 = st.columns([
                    1.0, 1.6, 1.4, 1.5, 1.5, 1.5, 1.7, 0.9
                ])

                with col1:
                    st.markdown("**Ticket**")
                with col2:
                    st.markdown("**Cliente**")
                with col3:
                    st.markdown("**Telefone**")
                with col4:
                    st.markdown("**Técnico Anterior**")
                with col5:
                    st.markdown("**Novo Técnico**")
                with col6:
                    st.markdown("**Data / Hora**")
                with col7:
                    st.markdown("**Evento**")
                with col8:
                    st.markdown("**Ação**")

                st.divider()

                for _, registro in df_auditoria.iterrows():
                    registro_id = registro["id"]

                    col1, col2, col3, col4, col5, col6, col7, col8 = st.columns([
                        1.0, 1.6, 1.4, 1.5, 1.5, 1.5, 1.7, 0.9
                    ])

                    with col1:
                        st.write(f"#{registro['ticket_id']}")
                    with col2:
                        st.write(registro["cliente"] or "-")
                    with col3:
                        st.write(registro["telefone"] or "-")
                    with col4:
                        st.write(registro["tecnico_anterior"] or "-")
                    with col5:
                        st.write(registro["tecnico_atual"] or "-")
                    with col6:
                        st.write(formatar_data_hora(registro["data_hora"]))
                    with col7:
                        st.write(registro["evento"] or "-")
                    with col8:
                        if st.button(
                            "🗑️",
                            key=f"excluir_auditoria_{registro_id}",
                            help=f"Excluir registro #{registro_id}"
                        ):
                            removido = excluir_auditoria_registro(registro_id)
                            if removido:
                                st.toast(
                                    "Registro de auditoria excluído.",
                                    icon="🗑️"
                                )
                                st.rerun()
                            else:
                                st.error(
                                    "Não foi possível excluir o registro."
                                )

                    st.divider()

    # ========================================================
    # EXPORTAÇÃO DA AUDITORIA
    # ========================================================

    st.write("")
    st.subheader("📄 Exportar auditoria")
    st.caption(
        "Exporte as transferências registradas no banco de auditoria. "
        "Mesmo sem registros, é possível gerar um CSV de teste "
        "contendo apenas os cabeçalhos."
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
        st.error(
            "❌ A data inicial não pode ser maior que a data final."
        )
    else:
        df_csv = consultar_auditoria(
            data_inicial=data_csv_inicial,
            data_final=data_csv_final,
            tecnico="Todos"
        )

        if df_csv.empty:
            st.info(
                "ℹ️ Nenhuma transferência encontrada no período. "
                "O CSV de teste conterá apenas os cabeçalhos."
            )
        else:
            st.success(
                f"✅ {len(df_csv)} transferência(s) "
                f"encontrada(s) no período."
            )

        arquivo_csv = gerar_csv(df_csv)

        nome_arquivo = (
            f"auditoria_transferencias_"
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
