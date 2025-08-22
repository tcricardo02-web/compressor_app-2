import streamlit as st
import logging
from dataclasses import dataclass
from typing import List, Dict, Optional
import pandas as pd
import plotly.graph_objects as go
import pint
import io
from datetime import datetime

from sqlalchemy import create_engine, Column, Integer, Float, ForeignKey
from sqlalchemy.orm import sessionmaker, relationship, declarative_base

from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as PDFImage
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet

import tempfile

# ------------------------------------------------------------------------------
# Logger e Unidades
# ------------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
ureg = pint.UnitRegistry()
Q_ = ureg.Quantity

# ------------------------------------------------------------------------------
# Sistema de Unidades
# ------------------------------------------------------------------------------
# O usuário pode escolher unidade para pressões, temperaturas e comprimentos.
unit_options = {
    "Opção 1": {"pressao": "psig", "temperatura": "°F", "comprimento": "polegadas"},
    "Opção 2": {"pressao": "kgf/cm²", "temperatura": "°C", "comprimento": "mm"}
}

selected_unit = st.sidebar.radio("Selecione o sistema de unidades", list(unit_options.keys()))
units = unit_options[selected_unit]
st.sidebar.write(f"**Pressão:** {units['pressao']}, **Temperatura:** {units['temperatura']}, **Comprimento:** {units['comprimento']}")

# ------------------------------------------------------------------------------
# Banco de dados e modelos (exemplo, não utilizado para persistência neste app)
# ------------------------------------------------------------------------------
DB_PATH = "sqlite:///compressor.db"
Base = declarative_base()
engine = create_engine(DB_PATH, echo=False, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)

class FrameModel(Base):
    __tablename__ = "frame"
    id = Column(Integer, primary_key=True, index=True)
    rpm = Column(Float)
    stroke_m = Column(Float)
    n_throws = Column(Integer)
    throws = relationship("ThrowModel", back_populates="frame")

class ThrowModel(Base):
    __tablename__ = "throw"
    id = Column(Integer, primary_key=True, index=True)
    frame_id = Column(Integer, ForeignKey("frame.id"))
    throw_number = Column(Integer)
    bore_m = Column(Float)
    clearance_m = Column(Float)
    VVCP = Column(Float)
    SACE = Column(Float)
    SAHE = Column(Float)

    frame = relationship("FrameModel", back_populates="throws")

class ActuatorModel(Base):
    __tablename__ = "actuator"
    id = Column(Integer, primary_key=True, index=True)
    power_available_kW = Column(Float)
    derate_percent = Column(Float)
    air_cooler_fraction = Column(Float)

def init_db():
    Base.metadata.create_all(bind=engine)
    logger.info("Banco de dados inicializado.")

# ------------------------------------------------------------------------------
# Dataclasses para cálculos
# ------------------------------------------------------------------------------
@dataclass
class Frame:
    rpm: float
    stroke: float
    n_cilindros: int  # renomeado para refletir a quantidade de cilindros

@dataclass
class Cilindro:
    cilindro_num: int
    stage: int
    clearance_pct: float
    SACE: float
    VVCP_pct: float

@dataclass
class Actuator:
    power_kW: float
    derate_percent: float
    air_cooler_fraction: float

@dataclass
class Motor:
    type: str  # "Gás Natural" ou "Elétrico"
    rpm: float
    derate_percent: float
    air_cooler_consumption_pct: float  # fixo em 4%

# ------------------------------------------------------------------------------
# Cálculos de performance (mantendo função original)
# ------------------------------------------------------------------------------
def clamp(n, a, b):
    return max(a, min(n, b))

def perform_performance_calculation(
    mass_flow: float,
    inlet_pressure: Q_,
    inlet_temperature: Q_,
    n_stages: int,
    PR_total: float,
    cilindros: List[Cilindro],
    stage_mapping: Dict[int, List[int]],
    actuator: Actuator,
) -> Dict:
    m_dot = mass_flow
    P_in = inlet_pressure.to(ureg.Pa).magnitude
    T_in = inlet_temperature.to(ureg.K).magnitude

    n = max(n_stages, 1)
    PR_base = PR_total ** (1.0 / n)

    gamma = 1.30
    cp = 2.0

    stage_details = []
    total_W_kW = 0.0
    # Mapeia por número de cilindro
    cilindros_por_num = {c.cilindro_num: c for c in cilindros}

    for stage in range(1, n + 1):
        P_in_stage = P_in * (PR_base ** (stage - 1))
        P_out_stage = P_in_stage * PR_base

        # Seleciona todos os cilindros designados para este estágio
        assigned = stage_mapping.get(stage, [])
        if assigned:
            # Média dos parâmetros (só um exemplo, similar à função original)
            clearance_avg = sum(cilindros_por_num[c].clearance_pct for c in assigned if c in cilindros_por_num) / len(assigned)
            SACE_avg = sum(cilindros_por_num[c].SACE for c in assigned if c in cilindros_por_num) / len(assigned)
            VVCP_avg = sum(cilindros_por_num[c].VVCP_pct for c in assigned if c in cilindros_por_num) / len(assigned)
        else:
            clearance_avg = SACE_avg = VVCP_avg = 0.0

        eta_isent = 0.65 + 0.15 * (SACE_avg / 100.0) - 0.05 * (VVCP_avg / 100.0) + 0.10 * (clearance_avg / 100.0)
        eta_isent = clamp(eta_isent, 0.65, 0.92)

        T_out_isent = T_in * (PR_base ** ((gamma - 1.0) / gamma))
        T_out_actual = T_in + (T_out_isent - T_in) / max(eta_isent, 1e-6)
        delta_T = T_out_actual - T_in

        W_stage = m_dot * cp * delta_T / 1000.0
        total_W_kW += W_stage

        stage_details.append({
            "stage": stage,
            "P_in_bar": P_in_stage / 1e5,
            "P_out_bar": P_out_stage / 1e5,
            "PR": PR_base,
            "T_in_C": T_in - 273.15,
            "T_out_C": T_out_actual - 273.15,
            "isentropic_efficiency": eta_isent,
            "shaft_power_kW": W_stage,
            "shaft_power_BHP": W_stage * 1.34102
        })

        T_in = T_out_actual

    return {
        "mass_flow_kg_s": m_dot,
        "inlet_pressure_bar": P_in / 1e5,
        "inlet_temperature_C": inlet_temperature.to(ureg.degC).magnitude,
        "n_stages": n_stages,
        "total_shaft_power_kW": total_W_kW,
        "total_shaft_power_BHP": total_W_kW * 1.34102,
        "stage_details": stage_details
    }

# ------------------------------------------------------------------------------
# Função para gerar diagrama ilustrativo da configuração do equipamento
# (semelhante ao Ariel 7)
# ------------------------------------------------------------------------------
def generate_config_diagram(motor: Motor, frame: Frame, cilindros: List[Cilindro]) -> go.Figure:
    fig = go.Figure()
    width, height = 900, 350

    # Diagrama Motor
    fig.add_shape(type="rect", x0=30, y0=height/2-25, x1=130, y1=height/2+25,
                  line=dict(color="MediumPurple"), fillcolor="Lavender")
    fig.add_annotation(x=80, y=height/2,
                       text=f"Motor<br>{motor.type}<br>RPM: {motor.rpm:.0f}",
                       showarrow=False, font=dict(size=12), align="center")

    # Diagrama Compressor (Frame)
    f_x, f_y, f_w, f_h = 180, height/2-25, 200, 50
    fig.add_shape(type="rect", x0=f_x, y0=f_y, x1=f_x+f_w, y1=f_y+f_h,
                  line=dict(color="RoyalBlue"), fillcolor="LightSkyBlue")
    fig.add_annotation(x=f_x+f_w/2, y=f_y+f_h/2,
                       text=f"Compressor\nStroke: {frame.stroke}\nCilindros: {frame.n_cilindros}",
                       showarrow=False, font=dict(size=12), align="center")
    
    # Diagrama para cada cilindro
    n = len(cilindros)
    if n > 0:
        spacing = f_w / n
        for c in cilindros:
            idx = c.cilindro_num - 1
            tx = f_x + idx*spacing + spacing/4
            ty = f_y + f_h + 20
            tw, th = spacing/2, 30
            fig.add_shape(type="rect", x0=tx, y0=ty, x1=tx+tw, y1=ty+th,
                          line=dict(color="DarkOrange"), fillcolor="Moccasin")
            fig.add_annotation(x=tx+tw/2, y=ty+th/2,
                               text=f"Cil {c.cilindro_num}\nEstágio: {c.stage}",
                               showarrow=False, font=dict(size=10))
    
    # Diagrama Air Cooler
    a_x, a_y, a_w, a_h = f_x+f_w+50, height/2-20, 120, 60
    fig.add_shape(type="rect", x0=a_x, y0=a_y, x1=a_x+a_w, y1=a_y+a_h,
                  line=dict(color="SaddleBrown"), fillcolor="PeachPuff")
    fig.add_annotation(x=a_x+a_w/2, y=a_y+a_h/2,
                       text="Air Cooler\nPerda: 1% por estágio\nSaída: 120°F",
                       showarrow=False, font=dict(size=12), align="center")

    fig.update_layout(width=width, height=height,
                      margin=dict(l=20, r=20, t=20, b=20),
                      xaxis=dict(visible=False), yaxis=dict(visible=False))
    return fig

# ------------------------------------------------------------------------------
# Função para gerar fluxograma (PFD) do processo
# ------------------------------------------------------------------------------
def generate_process_flow_diagram() -> go.Figure:
    fig = go.Figure()
    width, height = 900, 350

    # Bloco Compressor
    fig.add_shape(type="rect", x0=50, y0=50, x1=250, y1=150,
                  line=dict(color="RoyalBlue"), fillcolor="LightSkyBlue")
    fig.add_annotation(x=150, y=100,
                       text="Compressor",
                       showarrow=False, font=dict(size=14))

    # Bloco Air Cooler
    fig.add_shape(type="rect", x0=300, y0=50, x1=500, y1=150,
                  line=dict(color="SaddleBrown"), fillcolor="PeachPuff")
    fig.add_annotation(x=400, y=100,
                       text="Air Cooler\n(1% perda por estágio)\nSaída 120°F",
                       showarrow=False, font=dict(size=14))

    # Conexão entre Compressor e Air Cooler
    fig.add_annotation(x=275, y=100, text="Fluxo de Gás", showarrow=True,
                       arrowhead=2, ax=-20, ay=0)

    fig.update_layout(width=width, height=height,
                      margin=dict(l=20, r=20, t=20, b=20),
                      xaxis=dict(visible=False), yaxis=dict(visible=False))
    return fig

# ------------------------------------------------------------------------------
# Exporta PDF (mantém função original)
# ------------------------------------------------------------------------------
def export_to_pdf(results: Dict, fig: go.Figure) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4)
    elements = []
    styles = getSampleStyleSheet()
    title_style = styles["Heading1"]
    normal = styles["Normal"]

    elements.append(Paragraph("<b>[LOGO AQUI]</b>", styles["Title"]))
    elements.append(Spacer(1, 12))
    elements.append(Paragraph("Relatório de Performance do Compressor", title_style))
    elements.append(Spacer(1, 12))

    summary = [
        ["Massa (kg/s)", f"{results['mass_flow_kg_s']:.2f}"],
        ["Pressão In (bar)", f"{results['inlet_pressure_bar']:.2f}"],
        ["Temp In (°C)", f"{results['inlet_temperature_C']:.2f}"],
        ["Estágios", f"{results['n_stages']}"],
        ["Potência Total (kW)", f"{results['total_shaft_power_kW']:.2f}"],
        ["Potência Total (BHP)", f"{results['total_shaft_power_BHP']:.2f}"]
    ]
    table = Table(summary)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black)
    ]))
    elements.append(table)
    elements.append(Spacer(1, 12))

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmpfile:
        fig.write_image(tmpfile.name, format="png")
        elements.append(PDFImage(tmpfile.name, width=400, height=160))
        elements.append(Spacer(1, 12))

    data = [["Estágio", "P_in (bar)", "P_out (bar)", "PR", "T_in (°C)", "T_out (°C)", "Eficiência", "Potência (kW)"]]
    for s in results["stage_details"]:
        data.append([
            s["stage"], f"{s['P_in_bar']:.2f}", f"{s['P_out_bar']:.2f}", f"{s['PR']:.2f}",
            f"{s['T_in_C']:.1f}", f"{s['T_out_C']:.1f}", f"{s['isentropic_efficiency']:.2f}", f"{s['shaft_power_kW']:.2f}"
        ])
    stage_table = Table(data)
    stage_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black)
    ]))
    elements.append(stage_table)
    elements.append(Spacer(1, 24))

    footer = f"Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M:%S')} por CompressorCalc"
    elements.append(Paragraph(footer, normal))
    doc.build(elements)
    pdf = buffer.getvalue()
    buffer.close()
    return pdf

# ------------------------------------------------------------------------------
# Streamlit App com múltiplas abas
# ------------------------------------------------------------------------------
def main():
    st.set_page_config(page_title="Calculadora de Performance de Compressor", layout="wide")
    st.title("Calculadora de Performance de Compressor (Estilo Ariel 7)")

    init_db()

    # Uso de abas para separar funcionalidades
    tab_perf, tab_config, tab_processo = st.tabs(["Cálculo de Performance", "Configuração do Equipamento", "Processo"])

    # Aba de Configuração do Equipamento
    with tab_config:
        st.header("Configuração do Equipamento")
        st.subheader("Motor")
        motor_type = st.selectbox("Tipo de Motor", options=["Gás Natural", "Elétrico"])
        motor_rpm = st.number_input("RPM do Motor", value=900)
        motor_derate = st.number_input("Derate (%)", value=0.0)
        # Potência consumida pelo Air Cooler fixada em 4%
        air_cooler_consumption = st.number_input("Potência consumida pelo Air Cooler (% do motor)", value=4.0)

        motor = Motor(type=motor_type, rpm=motor_rpm, derate_percent=motor_derate, air_cooler_consumption_pct=air_cooler_consumption)

        st.subheader("Air Cooler")
        st.info("Air Cooler: Perda de carga de 1% por estágio, temperatura de saída fixa de 120°F por estágio de resfriamento.")

        st.subheader("Compressor")
        compressor_stroke = st.number_input(f"Stroke (em {units['comprimento']})", value=0.2)
        n_cilindros = st.number_input("Número de Cilindros", value=2, min_value=1, step=1)

        # Prepara os dados para cada cilindro
        cilindro_data = []
        for i in range(1, n_cilindros+1):
            col1, col2, col3, col4, col5 = st.columns(5)
            with col1:
                stage = st.number_input(f"Cil {i} - Estágio", min_value=1, step=1, key=f"cil_stage_{i}")
            with col2:
                clearance = st.number_input(f"Cil {i} - Clearance (%)", value=5.0, key=f"cil_clearance_{i}")
            with col3:
                sace = st.number_input(f"Cil {i} - SACE", value=5.0, key=f"cil_sace_{i}")
            with col4:
                vvcp = st.number_input(f"Cil {i} - VVCP (%)", value=5.0, key=f"cil_vvcp_{i}")
            with col5:
                st.write(" ")  # espaço
            cilindro_data.append({
                "cilindro_num": i,
                "stage": int(stage),
                "clearance_pct": float(clearance),
                "SACE": float(sace),
                "VVCP_pct": float(vvcp)
            })
        cilindros = [Cilindro(**data) for data in cilindro_data]

        # Diagrama ilustrativo da configuração do equipamento
        st.subheader("Diagrama de Configuração do Equipamento")
        config_fig = generate_config_diagram(motor, Frame(rpm=motor_rpm, stroke=compressor_stroke, n_cilindros=n_cilindros), cilindros)
        st.plotly_chart(config_fig, use_container_width=True)

    # Aba de Cálculo de Performance
    with tab_perf:
        st.header("Cálculo de Performance")
        mass_flow = st.number_input("Massa de entrada (kg/s)", value=10.0)
        # Para a pressão e temperatura de entrada, assumindo que se fornece em bar e °C; converter se necessário
        inlet_pressure = st.number_input("Pressão de entrada (bar)", value=1.0)
        inlet_temp = st.number_input("Temperatura de entrada (°C)", value=25.0)
        n_stages = st.number_input("Número de Estágios", value=2, min_value=1, step=1)
        PR_total = st.number_input("Relação de Pressão Total", value=4.0)

        # Mapeia os cilindros para os estágios (podem ser mais de um cilindro por estágio)
        stage_mapping = {}
        for c in cilindros:
            stage_mapping.setdefault(c.stage, []).append(c.cilindro_num)

        actuator = Actuator(power_kW=mass_flow, derate_percent=0.0, air_cooler_fraction=0.1)  # exemplo simples
        if st.button("Calcular Performance"):
            results = perform_performance_calculation(
                mass_flow,
                Q_(inlet_pressure, ureg.bar),
                Q_(inlet_temp, ureg.degC),
                int(n_stages),
                PR_total,
                cilindros,
                stage_mapping,
                actuator,
            )
            st.subheader("Resultados")
            st.json(results)
            # Diagrama original de performance (mantido ou adaptado se necessário)
            perf_fig = generate_config_diagram(motor, Frame(rpm=motor_rpm, stroke=compressor_stroke, n_cilindros=n_cilindros), cilindros)
            st.plotly_chart(perf_fig, use_container_width=True)
            df_results = pd.DataFrame(results["stage_details"])
            st.dataframe(df_results)

            # Downloads
            csv = df_results.to_csv(index=False).encode('utf-8')
            st.download_button("⬇️ Baixar CSV", csv, "resultados.csv", "text/csv")
            xlsx_buffer = io.BytesIO()
            with pd.ExcelWriter(xlsx_buffer, engine="xlsxwriter") as writer:
                df_results.to_excel(writer, index=False)
            st.download_button("⬇️ Baixar Excel", xlsx_buffer.getvalue(), "resultados.xlsx")
            pdf_bytes = export_to_pdf(results, perf_fig)
            st.download_button(
                label="⬇️ Baixar PDF",
                data=pdf_bytes,
                file_name="relatorio_compressor.pdf",
                mime="application/pdf"
            )

    # Aba de Processo (PFD)
    with tab_processo:
        st.header("Fluxograma do Processo - Compressor e Air Cooler")
        pfd_fig = generate_process_flow_diagram()
        st.plotly_chart(pfd_fig, use_container_width=True)
        st.markdown("""
        **Legenda:**
        - **Compressor:** Compressão dos gases.
        - **Air Cooler:** Resfriamento com perda de carga de 1% por estágio e saída fixada em 120°F.
        - **Fluxo de Gás:** Representada pela seta entre os blocos.
        """)

if __name__ == "__main__":
    main()
