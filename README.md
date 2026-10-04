# Radar de pesquisa de ações

Radar diário para organizar volume, preço e manchetes de ações da B3 e dos EUA. É uma ferramenta de pesquisa: **não recomenda compra ou venda, não prevê preços e não envia ordens**.

## Estrutura

```text
.
├── radar/
│   ├── config.py       # caminhos, limites e pesos compartilhados
│   ├── b3.py           # baixa COTAHIST e grava b3.csv e nomes_b3.csv
│   ├── eua.py          # interface para o coletor de preços dos EUA
│   ├── noticias.py     # interface para a coleta RSS e histórico
│   ├── candidatas.py   # seleciona até 60 maiores volumes relativos
│   ├── calculo.py      # interface para indicadores e percentis
│   ├── watchlist.py    # interface para o relatório Markdown
│   └── main.py         # orquestra a sequência completa
├── dados/              # arquivos CSV gerados durante as coletas
├── logs/               # radar.log
├── saida/              # watchlists Markdown
├── deploy/             # entrypoint e agenda cron da imagem
├── tests/              # testes sem chamadas externas
├── Dockerfile          # imagem Python e cron para EasyPanel
├── docker-compose.yml  # serviço e volumes persistentes
├── eua.py              # coleta de preços dos EUA
├── b3.py               # atalho do coletor COTAHIST
├── atencao.py          # consulta o RSS do Google Notícias
├── nota.py             # cálculo das notas e geração da watchlist
├── main.py             # comando de entrada do projeto
└── requirements.txt
```

Os arquivos de dados, logs e watchlists ficam fora do controle de versão por padrão. `b3.csv` e `eua.csv` têm as colunas `data`, `ticker`, `fechamento`, `quantidade` e volume financeiro (`volume_rs` na B3; `volume` nos EUA). Os arquivos `nomes_b3.csv` e `nomes_eua.csv` alimentam as consultas com nome empresarial.

## Instalação no Ubuntu

```bash
cd "/caminho/para/Radar Ações"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

O coletor dos EUA usa `yfinance`, uma biblioteca não oficial que lê dados do Yahoo Finance, para pesquisa pessoal. Disponibilidade, limites e termos de uso podem mudar; o coletor registra falhas e repete lotes, mas não garante que todos os símbolos retornem dados. A lista do Nasdaq Trader é filtrada para Nasdaq, NYSE e NYSE American, removendo ETFs, títulos de teste e instrumentos que não pareçam ações comuns.

## Primeira execução

Faça primeiro uma coleta reduzida dos EUA:

```bash
source .venv/bin/activate
python eua.py --limite 20 --saida dados/eua_teste.csv --nomes-saida dados/nomes_eua_teste.csv
```

Confira se o CSV tem cabeçalho, datas e volumes plausíveis. Para rodar o fluxo completo manualmente:

```bash
python main.py
```

O comando executa estas etapas em sequência:

1. Baixa até 60 pregões recentes da B3 pelo COTAHIST diário.
2. Baixa o histórico diário das ações selecionadas das listas da Nasdaq Trader e filtra preço acima de US$ 5 e média de volume financeiro acima de US$ 5 milhões.
3. Seleciona até 60 ações com maior volume relativo combinado entre os mercados e consulta as manchetes no RSS do Google Notícias.
4. Calcula volume relativo, retornos de 5 e 20 pregões, percentis separados por mercado e a nota ponderada.
5. Salva `saida/watchlist_AAAA-MM-DD.md` e registra a execução em `logs/radar.log`.

Para uma execução reduzida do pipeline, use `python main.py --limite-eua 20`. A B3 continua sendo coletada normalmente; a opção limita apenas a quantidade de símbolos dos EUA processados.

Rode a suíte local antes de publicar alterações:

```bash
python -m unittest discover -s tests -v
```

## Fórmula e dados incompletos

O volume relativo é o volume financeiro do pregão dividido pela média dos 20 pregões anteriores. O momento combina a média dos percentis dos retornos de 5 e 20 pregões. Notícias são a contagem de manchetes únicas nas últimas 24 horas; atenção é a razão entre essa contagem e a média das sete observações diárias anteriores.

```text
nota = 100 x (0,40 x percentil_volume
           + 0,25 x percentil_momento
           + 0,20 x percentil_noticias
           + 0,15 x percentil_atencao)
```

Quando a atenção está ausente ou parcial, o cálculo redistribui proporcionalmente os pesos dos componentes disponíveis e marca a nota como parcial. A seleção de notícias exige nome e ticker quando há nome cadastrado; se não houver nome, usa o ticker. Os percentis são calculados dentro de cada mercado e com dados disponíveis até a data da watchlist.

## Agendamento no VPS

### Deploy pelo GitHub no EasyPanel

O clone local precisa apontar para o repositório GitHub que você quer usar. No estado atual desta pasta, não há `origin` configurado. Para publicar em um repositório vazio, substitua o endereço de exemplo pelo seu e envie a branch:

```bash
git remote add origin git@github.com:SEU_USUARIO/SEU_REPOSITORIO.git
git branch -M main
git add -A
git commit -m "Prepare Radar for VPS deployment"
git push -u origin main
```

Se o repositório já tiver histórico, primeiro integre esta pasta à branch existente conforme o estado do repositório; não force o push nem sobrescreva commits remotos.

O repositório inclui uma imagem Docker com o cron do Linux configurado para executar de segunda a sexta às 21h30, no horário de Brasília. O processo de cron roda dentro do container; não configure um segundo cron no host para a mesma tarefa, pois isso duplicaria as coletas. A imagem não publica portas e não precisa de domínio ou rota no Traefik: o Radar só inicia conexões de saída para obter os dados.

1. Envie estes arquivos para a branch que será usada no deploy do seu GitHub.
2. No EasyPanel, crie um **Compose Service** e selecione **GitHub** como origem. Informe `owner/repo`, a branch, o build path `/` e o arquivo `docker-compose.yml`. Para repositório privado, autorize o acesso pelo método oferecido pelo EasyPanel.
3. Implante o serviço. O Compose constrói a imagem pelo `Dockerfile`, instala as dependências e inicia o cron.
4. Mantenha uma réplica do serviço. Cada réplica executa a própria agenda e pode duplicar as buscas e gravações.
5. Confirme os volumes persistentes `radar_dados`, `radar_logs` e `radar_saida`, montados em `/app/dados`, `/app/logs` e `/app/saida`. O `docker-compose.yml` já declara esses volumes.
6. Não configure domínio nem portas públicas. O painel do EasyPanel pode acompanhar o container; os arquivos de log ficam no volume persistente.

Ative o auto deploy do GitHub se quiser que cada push faça um novo deploy. Os volumes sobrevivem à recriação do container, mas não substituem backup do VPS. Inclua principalmente `radar_dados`, pois ele guarda o histórico de atenção e as cotações usadas nas análises; a documentação do EasyPanel recomenda definir recuperação para volumes declarados em Compose.

O cron está definido em `deploy/radar.cron` e usa a zona `America/Sao_Paulo` configurada na imagem. O bloqueio `flock` impede que duas execuções se sobreponham. O log principal fica em `/app/logs/radar.log`; mensagens do agendador ou de inicialização ficam em `/app/logs/cron.log`.

Para conferir se executou, abra o terminal do serviço no EasyPanel e rode:

```bash
tail -n 100 /app/logs/radar.log
tail -n 50 /app/logs/cron.log
```

Confira também o horário do último registro e se há uma watchlist nova em `/app/saida`. No GitHub, a aba **Actions** mostra os testes executados após cada push e pull request; no EasyPanel, a tela do serviço mostra o resultado do último deploy.

Se o VPS estiver desligado às 21h30, o cron não recupera a execução perdida quando o servidor volta. Depois que o VPS estiver online, abra o terminal do serviço no EasyPanel e rode `python /app/main.py` para recuperar manualmente aquele dia. Se a B3 ainda não tiver publicado o arquivo ou algum provedor estiver indisponível, corrija a causa e execute novamente.

### Cron no host, sem o container agendador

Use este modo somente se optar por rodar o clone do repositório diretamente no Ubuntu. Crie ambiente virtual e instale `requirements.txt` conforme a seção de instalação. Depois edite o crontab do usuário que executará o Radar:

```bash
crontab -e
```

Adicione uma linha, trocando `/opt/radar` pelo caminho real do clone. O `CRON_TZ` define Brasília independentemente do fuso padrão do host:

```cron
CRON_TZ=America/Sao_Paulo
30 21 * * 1-5 cd /opt/radar && /usr/bin/flock -n /opt/radar/logs/radar.lock /opt/radar/.venv/bin/python /opt/radar/main.py >> /opt/radar/logs/cron.log 2>&1
```

Confira a agenda com `crontab -l` e os registros com `tail -n 100 /opt/radar/logs/radar.log` e `tail -n 50 /opt/radar/logs/cron.log`. Se o computador ficar desligado, essa execução também é perdida; rode `/opt/radar/.venv/bin/python /opt/radar/main.py` manualmente quando voltar. Não use este crontab do host junto com o cron já ativo no serviço Docker.

## Limites e revisão

- O COTAHIST é filtrado pelos códigos de mercado e lote padrão usados no roteiro de referência. Dados ausentes, formato alterado ou desdobramentos podem exigir revisão.
- O universo dos EUA deriva das listas de símbolos da Nasdaq Trader; a classificação de ações comuns se baseia nos campos e nomes dessas listas.
- RSS pode conter resultados irrelevantes, duplicados ou incompletos; a contagem é uma aproximação de atenção.
- Pesos e percentis são parâmetros de pesquisa, não evidência de desempenho futuro.
- Verifique os CSVs e as primeiras watchlists antes de confiar nos resultados. A lista não é recomendação de investimento.
