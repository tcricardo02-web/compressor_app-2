UniCompSim - Simulador Universal de Compressores Alternativos

Esta é uma aplicação web construída com Streamlit para simular o desempenho de compressores alternativos.

Como Executar

1. Implantação no Streamlit Community Cloud (Recomendado)

Crie um repositório no GitHub: Faça o upload dos arquivos app.py, requirements.txt e README.md para um novo repositório público no seu GitHub.

Acesse o Streamlit Community Cloud: Vá para share.streamlit.io.

Implante o App: Clique em "New app", conecte sua conta do GitHub, selecione o repositório que você criou e o arquivo principal (app.py). Clique em "Deploy!".

Seu aplicativo estará online e acessível publicamente em poucos minutos.

2. Execução Local

Pré-requisitos: Certifique-se de ter o Python 3.8+ instalado.

Crie um ambiente virtual (opcional, mas recomendado):

python -m venv venv
source venv/bin/activate  # No Windows: venv\Scripts\activate


Instale as dependências:

pip install -r requirements.txt


Execute o aplicativo:

streamlit run app.py


O aplicativo será aberto automaticamente no seu navegador.
