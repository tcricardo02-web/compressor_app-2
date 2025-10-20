import streamlit as st
import math
import plotly.graph_objects as go

# --- Configuração da Página ---
st.set_page_config(
    page_title="UniCompSim",
    page_icon="🔩",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- Funções de Cálculo (Motor da Simulação) ---
def run_simulation(inputs):
    """
    Executa a simulação de desempenho do compressor com base nas entradas.
    Esta é a versão em Python da lógica de cálculo do JavaScript.
    """
    i = inputs

    # 1. Propriedades do Gás (estimativa simplificada para gás natural típico)
    molar_mass = (i['gas']['ch4'] * 16.04 + i['gas']['c2h6'] * 30.07 + i['gas']['c3h8'] * 44.1 + i['gas']['n2'] * 28.01) / 100
    k = 1.28  # Razão de calores específicos (k), valor típico
    z_avg = 0.95  # Fator de compressibilidade médio, valor típico

    # 2. Condições de Processo (conversão para unidades SI)
    ps_abs = (i['op']['ps'] + 1.01325) * 1e5  # Pa
    pd_abs = (i['op']['pd'] + 1.01325) * 1e5  # Pa
    ts_abs = i['op']['ts'] + 273.15  # K
    
    if ps_abs == 0: return None # Evita divisão por zero
    compression_ratio = pd_abs / ps_abs

    # 3. Geometria do Cilindro
    stroke_m = i['comp']['stroke'] / 1000
    bore_m = i['cyl']['bore'] / 1000
    rod_m = i['cyl']['rod'] / 1000
    area_he = math.pi * (bore_m / 2)**2  # m^2
    area_ce = area_he - (math.pi * (rod_m / 2)**2)  # m^2
    displacement_he = area_he * stroke_m * (i['comp']['rpm'] / 60)  # m^3/s
    displacement_ce = area_ce * stroke_m * (i['comp']['rpm'] / 60)  # m^3/s

    # 4. Cálculos de Performance
    vol_eff_he = 1 - (i['cyl']['clearanceHE'] / 100) * (compression_ratio**(1 / k) - 1)
    vol_eff_ce = 1 - (i['cyl']['clearanceCE'] / 100) * (compression_ratio**(1 / k) - 1)
    
    # Garante que a eficiência volumétrica não seja negativa
    vol_eff_he = max(0, vol_eff_he)
    vol_eff_ce = max(0, vol_eff_ce)

    flow_m3s = (displacement_he * vol_eff_he) + (displacement_ce * vol_eff_ce)
    
    # Conversão de Vazão para MMSCFD (aproximado)
    flow_mmscfd = flow_m3s * (ps_abs / 101325) * (288.7 / ts_abs) * (3600 * 24 / 0.0283168) / 1e6

    td_abs = ts_abs * compression_ratio**((k - 1) / (k * z_avg))
    td_c = td_abs - 273.15

    gas_power_w = (ps_abs * flow_m3s * (k / (k - 1))) * (compression_ratio**((k - 1) / k) - 1) / z_avg if k > 1 else 0
    
    brake_power_kw = (gas_power_w * 1.10) / 1000

    rod_load_comp = (pd_abs * area_ce) - (ps_abs * area_he)
    rod_load_tens = (pd_abs * area_he) - (ps_abs * area_ce)
    max_rod_load_kn = max(abs(rod_load_comp), abs(rod_load_tens)) / 1000

    # --- Montagem do Objeto de Resultados ---
    results = {
        'power': brake_power_kw,
        'power_perc': (brake_power_kw / i['comp']['powerLimit']) * 100 if i['comp']['powerLimit'] > 0 else 0,
        'rod_load': max_rod_load_kn,
        'rod_load_perc': (max_rod_load_kn / i['comp']['rodloadLimit']) * 100 if i['comp']['rodloadLimit'] > 0 else 0,
        'flow': flow_mmscfd,
        'flow_perc': (flow_mmscfd / i['op']['flowTarget']) * 100 if i['op']['flowTarget'] > 0 else 0,
        'temp': td_c,
        'pv_data': {
            'ps': i['op']['ps'],
            'pd': i['op']['pd'],
            'clearance': i['cyl']['clearanceHE']
        },
        'rod_load_data': {
            'comp': rod_load_comp / 1000,
            'tens': rod_load_tens / 1000,
            'limit': i['comp']['rodloadLimit']
        }
    }
    return results

# --- Funções de Gráfico ---
def create_pv_chart(data):
    """Gera um diagrama P-V teórico simplificado com Plotly."""
    c = data['clearance'] / 100
    k = 1.28
    rc = (data['pd'] + 1.01325) / (data['ps'] + 1.01325) if (data['ps'] + 1.01325) > 0 else 1
    
    v_total = 1 + c
    
    # Pontos do ciclo
    v_suc_end = v_total
    p_suc = data['ps']
    
    v_comp_end = c
    p_comp_end = data['pd']
    
    v_exp_start = c
    p_exp_start = data['pd']

    v_exp_end = c * (rc**(1/k))

    # Curva de compressão
    comp_v = [v_total * (1 - 0.01 * i) for i in range(101)]
    comp_p = [p_suc * (v_total/v)**k for v in comp_v if v > c]
    comp_v_filt = [v for v in comp_v if v > c]

    # Curva de expansão
    exp_v = [c * (1 + 0.01 * i * ((rc**(1/k))-1) ) for i in range(101)]
    exp_p = [p_exp_start * (v_exp_start/v)**k for v in exp_v]

    fig = go.Figure()
    # Adiciona as curvas
    fig.add_trace(go.Scatter(x=[v_exp_end, v_total], y=[p_suc, p_suc], mode='lines', name='Sucção', line=dict(color='blue')))
    fig.add_trace(go.Scatter(x=comp_v_filt, y=comp_p, mode='lines', name='Compressão', line=dict(color='red')))
    fig.add_trace(go.Scatter(x=[c, c], y=[p_suc, p_comp_end], mode='lines', name='Descarga', line=dict(color='green')))
    fig.add_trace(go.Scatter(x=exp_v, y=exp_p, mode='lines', name='Expansão', line=dict(color='purple')))

    fig.update_layout(
        title='Diagrama P-V (Teórico)',
        xaxis_title='Volume (relativo)',
        yaxis_title='Pressão (barg)',
        showlegend=False,
        margin=dict(l=20, r=20, t=40, b=20)
    )
    return fig


def create_rod_load_chart(data):
    """Gera um gráfico de carga na haste senoidal simplificado com Plotly."""
    angles = list(range(0, 361, 10))
    load_gas = [(data['tens'] + data['comp'])/2 + (data['tens'] - data['comp'])/2 * math.cos(math.radians(a)) for a in angles]
    
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=angles, y=load_gas, mode='lines', name='Carga de Gás', line=dict(color='rgb(79, 70, 229)')))
    
    # Linhas de limite
    fig.add_hline(y=data['limit'], line_dash="dash", line_color="red", annotation_text="Limite Tensão")
    fig.add_hline(y=-data['limit'], line_dash="dash", line_color="red", annotation_text="Limite Compressão")
    
    fig.update_layout(
        title='Carga na Haste (Teórico)',
        xaxis_title='Ângulo do Virabrequim (°)',
        yaxis_title='Carga na Haste (kN)',
        showlegend=True,
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01),
        margin=dict(l=20, r=20, t=40, b=20)
    )
    return fig


# --- Interface do Usuário (UI) ---
st.sidebar.title("UniCompSim")
st.sidebar.markdown("### Configuração da Simulação")

inputs = {}

# Módulo 1: Propriedades do Gás
with st.sidebar.expander("📥 1. Propriedades do Gás", expanded=True):
    st.markdown("Composição do Gás (Fração Molar %)")
    c1, c2 = st.columns(2)
    inputs_gas = {
        'ch4': c1.number_input("Metano (CH4)", value=85.0, min_value=0.0, max_value=100.0, step=1.0),
        'c2h6': c2.number_input("Etano (C2H6)", value=10.0, min_value=0.0, max_value=100.0, step=1.0),
        'c3h8': c1.number_input("Propano (C3H8)", value=5.0, min_value=0.0, max_value=100.0, step=1.0),
        'n2': c2.number_input("Nitrogênio (N2)", value=0.0, min_value=0.0, max_value=100.0, step=1.0),
    }
    inputs['gas'] = inputs_gas
    
# Módulo 2: Condições de Operação
with st.sidebar.expander("⚙️ 2. Condições de Operação", expanded=True):
    c1, c2 = st.columns(2)
    inputs_op = {
        'ps': c1.number_input("Pressão Sucção (barg)", value=20.0, step=1.0),
        'ts': c2.number_input("Temp. Sucção (°C)", value=30.0, step=1.0),
        'pd': c1.number_input("Pressão Descarga (barg)", value=60.0, step=1.0),
        'flowTarget': c2.number_input("Vazão Requerida (MMSCFD)", value=15.0, step=1.0),
    }
    inputs['op'] = inputs_op

# Módulo 3: Configuração do Compressor
with st.sidebar.expander("🔩 3. Configuração do Compressor", expanded=True):
    st.markdown("**Frame:**")
    c1, c2 = st.columns(2)
    inputs_comp = {
        'stroke': c1.number_input("Curso (mm)", value=150.0, step=1.0),
        'rpm': c2.number_input("RPM", value=1200, step=10),
        'rodloadLimit': c1.number_input("Carga Haste Máx (kN)", value=250.0, step=1.0),
        'powerLimit': c2.number_input("Potência Frame Máx (kW)", value=1000.0, step=10.0),
    }
    inputs['comp'] = inputs_comp

    st.markdown("**Cilindro (1º Estágio):**")
    c1, c2 = st.columns(2)
    inputs_cyl = {
        'bore': c1.number_input("Diâmetro Cil. (mm)", value=200.0, step=1.0),
        'rod': c2.number_input("Diâmetro Haste (mm)", value=50.0, step=1.0),
        'clearanceHE': c1.number_input("Folga Fixa HE (%)", value=15.0, step=0.5),
        'clearanceCE': c2.number_input("Folga Fixa CE (%)", value=15.0, step=0.5),
    }
    inputs['cyl'] = inputs_cyl

# Botão de Simulação
simulate_btn = st.sidebar.button("Simular Desempenho", type="primary", use_container_width=True)

# --- Área de Exibição Principal ---
st.title("Resultados da Simulação")

if simulate_btn:
    results = run_simulation(inputs)
    if results:
        # Painel de Resumo
        st.subheader("Painel de Resumo")
        
        def get_metric_color_help(percentage, limit=100):
            if percentage >= limit:
                return "error", "Limite excedido!"
            elif percentage > (limit * 0.9):
                return "warning", "Próximo ao limite."
            else:
                return "normal", "Dentro do limite."

        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            color, help_text = get_metric_color_help(results['power_perc'])
            st.metric(
                label="Potência Requerida", 
                value=f"{results['power']:.1f} kW", 
                delta=f"{results['power_perc']:.1f}% do limite", 
                delta_color=color,
                help=help_text
            )
        with col2:
            color, help_text = get_metric_color_help(results['rod_load_perc'])
            st.metric(
                label="Carga Máx. na Haste", 
                value=f"{results['rod_load']:.1f} kN", 
                delta=f"{results['rod_load_perc']:.1f}% do limite", 
                delta_color=color,
                help=help_text
            )
        with col3:
            st.metric(
                label="Vazão Calculada", 
                value=f"{results['flow']:.2f} MMSCFD",
                delta=f"{results['flow_perc']:.1f}% da meta",
                delta_color="off"
            )
        with col4:
            color, help_text = get_metric_color_help(results['temp'], limit=150)
            st.metric(
                label="Temp. de Descarga", 
                value=f"{results['temp']:.1f} °C",
                help=f"Limite de referência: 150 °C. {help_text}"
            )
            
        st.divider()

        # Gráficos
        st.subheader("Gráficos Interativos")
        gcol1, gcol2 = st.columns(2)
        with gcol1:
            pv_fig = create_pv_chart(results['pv_data'])
            st.plotly_chart(pv_fig, use_container_width=True)
        with gcol2:
            rodload_fig = create_rod_load_chart(results['rod_load_data'])
            st.plotly_chart(rodload_fig, use_container_width=True)
            
    else:
        st.error("Erro na simulação. Verifique se a pressão de sucção não é zero.")

else:
    st.info("Preencha os dados de configuração na barra lateral e clique em 'Simular' para ver os resultados.")
    st.markdown("---")
    st.image("https://images.unsplash.com/photo-1614275113714-a8a9a4561081?q=80&w=1932&auto=format&fit=crop", 
             caption="[Imagem de um compressor industrial]",
             use_column_width=True)


