# Radar de pesquisa de ações

Radar diário para organizar volume, preço e manchetes de ações da B3 e dos EUA. É uma ferramenta de pesquisa: **não recomenda compra ou venda, não prevê preços e não envia ordens**.

## Estrutura

```text
.
├── radar/
│   ├── config.py       # caminhos, limites e pesos compartilhados
│   ├── b3.py           # baixa COTAHIST e grava somente b3.csv
│   ├── eua.py          # interface para o coletor de preços dos EUA
│   ├── noticias.py     # interface para a coleta RSS e histórico
│   ├── candidatas.py   # seleciona até 60 maiores volumes relativos
│   ├── calculo.py      # interface para indicadores e percentis
│   ├── watchlist.py    # interface para os relatórios Markdown e CSV
│   └── main.py         # orquestra a sequência completa
├── referencias/        # cadastros locais usados no RSS e no enriquecimento do dashboard
├── dados/              # arquivos CSV gerados durante as coletas
├── logs/               # radar.log
├── saida/              # watchlists Markdown/CSV e relatórios de validação
├── deploy/             # entrypoint e agenda cron da imagem
├── tests/              # testes sem chamadas externas
├── Dockerfile          # imagem Python e cron para EasyPanel
├── docker-compose.yml  # serviço e volumes persistentes
├── eua.py              # coleta de preços dos EUA
├── b3.py               # atalho do coletor COTAHIST
├── atencao.py          # consulta o RSS do Google Notícias
├── nota.py             # cálculo das notas e geração da watchlist
├── main.py             # comando de entrada do projeto
├── dashboard.py        # página visual que lê as watchlists Markdown
├── validacao.py        # compara watchlists com retornos futuros do universo do dia
└── requirements.txt
```

Os históricos gerados, logs e watchlists ficam fora do controle de versão por padrão. `b3.csv` e `eua.csv` têm as colunas `data`, `ticker`, `fechamento`, `quantidade` e volume financeiro (`volume_rs` na B3; `volume` nos EUA). As buscas de notícias usam ticker e nome empresarial obtido dos cadastros locais em `referencias/`; esses nomes não são acrescentados aos históricos de cotações nem às watchlists. O dashboard também lê esses cadastros para completar empresa, bolsa e descrição quando apresenta uma watchlist. O Radar não baixa nem grava esses dados empresariais durante a coleta.

## Instalação no Ubuntu

```bash
cd "/caminho/para/Radar Ações"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

No macOS, use `python3 -m venv .venv`, `source .venv/bin/activate` e `python -m pip install -r requirements.txt`. No Windows PowerShell, use `py -3 -m venv .venv`, `.\.venv\Scripts\Activate.ps1` e `python -m pip install -r requirements.txt`.

O coletor dos EUA usa `yfinance`, uma biblioteca não oficial que lê dados do Yahoo Finance, para pesquisa pessoal. Disponibilidade, limites e termos de uso podem mudar. O coletor repete lotes e só substitui o CSV de produção quando a coleta termina com dados; se um lote falhar, preserva o CSV anterior. As duas listas oficiais do Nasdaq Trader são filtradas para remover ETFs, títulos de teste e instrumentos que não pareçam ações comuns. Splits informados pelo Yahoo nos últimos 21 pregões e variações de preço acima de 40% em 20 variações recentes são excluídos temporariamente para evitar distorções que exigem revisão. Ações com nome ausente no cadastro local ainda podem ser pesquisadas pelo ticker; a observação fica marcada como parcial.

## Primeira execução

Faça primeiro uma coleta reduzida dos EUA:

```bash
source .venv/bin/activate
python eua.py --limite 20 --saida dados/eua_teste.csv
```

Confira se o CSV tem cabeçalho, datas e volumes plausíveis. Para rodar o fluxo completo manualmente:

```bash
python main.py
```

O comando executa estas etapas em sequência:

1. Baixa até 60 pregões recentes da B3 pelo COTAHIST diário.
2. Baixa o histórico diário das ações selecionadas das listas da Nasdaq Trader e filtra preço acima de US$ 5 e média de volume financeiro acima de US$ 5 milhões.
3. Seleciona até 60 ações com maior volume relativo combinado entre os mercados e consulta as manchetes no RSS do Google Notícias.
4. Calcula volume relativo, retornos de 5 e 20 pregões, percentis separados por mercado e a nota ponderada. Na B3, BDRs e ações com salto absoluto superior a 40% em uma das últimas 20 variações são excluídos. Nos EUA, ações com split informado recentemente ou salto superior a 40% em 20 variações são excluídas até revisão, para reduzir distorções de eventos corporativos.
5. Salva `saida/watchlist_AAAA-MM-DD.md` e `saida/watchlist_AAAA-MM-DD.csv`, além de registrar a execução em `logs/radar.log`.

Se a atualização de um mercado falhar, o Radar tenta gerar uma watchlist com o histórico daquele mercado já existente e o mercado que conseguiu atualizar. Se a etapa de notícias falhar, gera a watchlist com os componentes disponíveis. A execução fica registrada como incompleta (código diferente de zero e sem atualizar `ultimo_sucesso.txt`); confira o log antes de considerar a saída completa. A ausência de um dos CSVs de cotações omite esse mercado em vez de interromper o outro.

`python main.py --limite-eua 20` executa apenas uma coleta de teste dos EUA e grava em `dados/eua_teste.csv`. Não atualiza os dados de produção nem gera watchlist. O mesmo vale para `python eua.py --limite 20`; para outro destino de teste, informe `--saida`. Uma coleta com limite não pode sobrescrever `dados/eua.csv`.

Rode a suíte local antes de publicar alterações:

```bash
python -m unittest discover -s tests -v
```

## Fórmula e dados incompletos

O volume relativo é o volume financeiro do pregão dividido pela média dos 20 pregões anteriores. O momento combina a média dos percentis dos retornos de 5 e 20 pregões. Notícias são a contagem de manchetes únicas nas últimas 24 horas. Atenção é a razão entre essa contagem e a média das sete execuções diárias completas anteriores de segunda a sexta. Dias ausentes ou buscas parciais não são tratados como zero; se faltar qualquer uma das sete observações, a nota fica parcial e usa a contagem de notícias disponível.

```text
nota = 100 x (0,40 x percentil_volume
           + 0,25 x percentil_momento
           + 0,20 x percentil_noticias
           + 0,15 x percentil_atencao)
```

Quando notícias ou atenção estão ausentes ou parciais, o cálculo redistribui proporcionalmente os pesos dos componentes completos e marca a nota como parcial. A busca RSS usa o ticker e o nome empresarial disponível no cadastro local; se não houver nome cadastrado, consulta o ticker e marca o registro como parcial. A watchlist Markdown e seu CSV correspondente contêm as 15 maiores notas de cada mercado, com ticker, nota, volume relativo, retorno de 5 pregões, notícias das últimas 24 horas e motivo. Os percentis são calculados dentro de cada mercado e com dados disponíveis até a data da watchlist.

## Validação histórica

Depois de acumular watchlists e cotações, compare as 15 maiores notas com o universo disponível no mesmo pregão:

```bash
python validacao.py
```

O relatório `saida/validacao_AAAA-MM-DD.md` separa B3 e EUA e calcula retornos líquidos após custo total de 0,1% por compra e venda nos 5 e 20 pregões seguintes. Mostra média e mediana dos retornos por ação e das médias diárias da carteira, retorno médio do universo, excesso médio, porcentagem de ações com retorno positivo, porcentagem das ações selecionadas que superaram a média do mercado, frequência em que a carteira supera o universo e pior caso. Cada carteira e o universo usam pesos iguais. Datas sem cotações futuras suficientes aparecem como pendentes; ações sem preço no pregão-alvo são contabilizadas como faltantes. O diagnóstico de ruído calcula um intervalo t de 95% para o excesso médio usando janelas não sobrepostas; o relatório informa quantas janelas foram usadas e marca o resultado como compatível com ruído quando o intervalo inclui zero. Com menos de duas janelas, o diagnóstico fica inconclusivo. É uma medida estatística simples, não uma prova de desempenho nem uma previsão. O CSV de preços precisa manter o pregão de entrada e os pregões futuros para que a avaliação possa ser calculada.

Acumule pelo menos 4 a 8 semanas de watchlists antes de interpretar as métricas. O número de janelas não sobrepostas exibido no diagnóstico mostra quanto histórico efetivamente sustenta cada horizonte; o horizonte de 20 pregões costuma ter menos janelas independentes no mesmo período. Mesmo com custos, amostras curtas têm incerteza e não demonstram desempenho futuro. O script é um diário descritivo, não uma conclusão de investimento.

## Agendamento no VPS

### Deploy pelo GitHub no EasyPanel

O repositório `automacaopaivaimobiliaria26/radar_acoes` contém o projeto. O Docker Compose mantém o coletor agendado e acrescenta o serviço web do dashboard. O cron do Radar executa de segunda a sexta às 21h30, no horário de Brasília; não configure um segundo cron no host.

1. Envie estes arquivos para a branch que será usada no deploy do seu GitHub.
2. No EasyPanel, atualize a origem GitHub do **Compose Service existente** para `automacaopaivaimobiliaria26/radar_acoes`, branch `main`, build path `/` e Compose file `docker-compose.yml`. Atualizar o mesmo serviço preserva o volume que já contém as watchlists; um Compose Service novo criaria outro conjunto de volumes.
3. Faça o Deploy. O Compose constrói a imagem pelo `Dockerfile` e inicia o coletor agendado e o dashboard.
4. Mantenha apenas uma instância do serviço `radar`, para evitar execuções duplicadas. O dashboard lê `/app/saida` em modo somente leitura.
5. Preserve os volumes `radar_dados`, `radar_logs` e `radar_saida`, montados em `/app/dados`, `/app/logs` e `/app/saida`.
6. O serviço do Radar não recebe domínio nem porta pública. Para o dashboard, configure no EasyPanel o domínio `www.paivanegociosimobiliarios.com.br`, caminho `/radar`, serviço interno `dashboard` e porta `8000`. Assim a página fica em `https://www.paivanegociosimobiliarios.com.br/radar` e a raiz do site atual permanece intacta.
7. Ative **Basic Auth** na seção **Security** do Compose Service, com um usuário e uma senha fortes. Não coloque a senha no GitHub nem em arquivos do projeto. O dashboard não publica porta no host; o tráfego externo entra pelo proxy do EasyPanel/Traefik.

### Homologação isolada do dashboard

Antes de adicionar o dashboard ao serviço oficial, valide a interface em um Compose Service separado. O arquivo `docker-compose.homolog.yml` monta apenas `tests/fixtures/watchlists`, não inicia o coletor e não acessa os volumes de produção.

1. Crie no EasyPanel um serviço Compose de homologação separado, apontando para uma branch que contenha esta versão.
2. Use `docker-compose.homolog.yml` como Compose file e mantenha o domínio de teste distinto do domínio oficial. Não vincule ainda `www.paivanegociosimobiliarios.com.br/radar`.
3. Ative Basic Auth nas configurações de segurança do EasyPanel e associe um domínio temporário ao serviço `dashboard-homolog`, porta interna `8000`.
4. Faça o deploy e confira a watchlist de demonstração de 02/10/2026, os cartões, as tabelas dos dois mercados, o seletor de data e a proteção por senha.
5. Ao terminar, remova o serviço de homologação. Depois da validação, a promoção para `https://www.paivanegociosimobiliarios.com.br/radar` requer confirmação explícita e deve atualizar o Compose Service oficial existente, preservando os volumes.

O fixture usa dados ilustrativos e não deve ser interpretado como coleta ou recomendação. A homologação não executa `main.py` nem escreve nos dados reais.

Ative o auto deploy do GitHub se quiser que cada push faça um novo deploy. Os volumes sobrevivem à recriação do container, mas não substituem backup do VPS. Inclua principalmente `radar_dados`, pois ele guarda o histórico de atenção e as cotações usadas nas análises; a documentação do EasyPanel recomenda definir recuperação para volumes declarados em Compose.

O dashboard lista os arquivos `watchlist_AAAA-MM-DD.md` em `/app/saida`, abre por padrão o mais recente e permite selecionar datas anteriores. Os cartões e tabelas são montados diretamente a partir das tabelas Markdown, sem alterar os arquivos gerados pelo Radar. Clique em um ticker na tabela, nos cartões de maior nota ou no gráfico de barras para abrir a visão individual do ativo. Essa visão carrega sob demanda o Advanced Chart do TradingView, com gráfico diário, volume, indicador de volume de 24 horas, barra de ferramentas e controles avançados, e fornece um botão para voltar à watchlist. O símbolo da B3 usa o prefixo `BMFBOVESPA`; nos EUA, a bolsa do cadastro local determina `NASDAQ`, `NYSE` ou `AMEX`. O widget exige conexão com `s3.tradingview.com`; disponibilidade e atraso dos dados dependem da bolsa e do TradingView. Se a bolsa não estiver no cadastro ou o componente externo não carregar, o dashboard oferece a busca ou o gráfico do ticker no site do TradingView.

Para recalcular uma watchlist histórica usando os CSVs já presentes no volume persistente, informe a data do pregão. O arquivo de saída com essa data é substituído atomicamente ao final do cálculo:

```bash
python /app/b3.py
python /app/eua.py
python /app/nota.py --data 2026-10-02
```

Atualizar primeiro os dois históricos restaura o universo completo depois de uma execução de teste antiga. A última linha recalcula preços e indicadores até a data informada e substitui o arquivo em `/app/saida`. Manchetes históricas só aparecem se as contagens daquela data estiverem em `/app/dados/atencao_hist.csv`; o Radar não consegue reconstruir depois a janela original de notícias das últimas 24 horas.

O cron está definido em `deploy/radar.cron` e usa a zona `America/Sao_Paulo` configurada na imagem. Ele inicia às 21h30 de segunda a sexta e verifica a cada hora e na inicialização se o horário diário passou sem nenhuma tentativa. A recuperação roda uma vez, sem duplicar uma tentativa já iniciada; uma execução que falhou precisa ser corrigida e iniciada manualmente. Os carimbos ficam em `/app/dados/ultima_tentativa.txt` e `/app/dados/ultimo_sucesso.txt`. O bloqueio `flock` impede que duas execuções se sobreponham. O log principal fica em `/app/logs/radar.log`; mensagens do agendador ou de inicialização ficam em `/app/logs/cron.log`.

Para conferir se executou, abra o terminal do serviço no EasyPanel e rode:

```bash
tail -n 100 /app/logs/radar.log
tail -n 50 /app/logs/cron.log
```

Confira também o horário do último registro e se há uma watchlist nova em `/app/saida`. No GitHub, a aba **Actions** mostra os testes executados após cada push e pull request; no EasyPanel, a tela do serviço mostra o resultado do último deploy.

Se o VPS estiver desligado às 21h30, a verificação horária ou a inicialização recupera a execução quando o serviço volta, desde que ainda não haja tentativa registrada naquele horário. Uma execução que já começou e falhou não é repetida automaticamente: consulte o log, corrija a causa e rode `python /app/main.py` manualmente.

### Cron no host, sem o container agendador

Use este modo somente se optar por rodar o clone do repositório diretamente no computador. Crie ambiente virtual e instale `requirements.txt` conforme a seção de instalação. No Linux, defina `CRON_TZ=America/Sao_Paulo`; no macOS, configure o fuso horário do sistema para Brasília e omita essa linha. Depois edite o crontab do usuário que executará o Radar:

```bash
crontab -e
```

Adicione as linhas abaixo, trocando `/opt/radar` pelo caminho real do clone. No macOS, omita a linha `CRON_TZ`:

```cron
CRON_TZ=America/Sao_Paulo
30 21 * * 1-5 cd /opt/radar && /usr/bin/flock -n /opt/radar/logs/radar.lock /opt/radar/.venv/bin/python /opt/radar/main.py >> /opt/radar/logs/cron.log 2>&1
```

Confira a agenda com `crontab -l` e os registros com `tail -n 100 /opt/radar/logs/radar.log` e `tail -n 50 /opt/radar/logs/cron.log`. Se o computador ficar desligado, essa execução também é perdida; rode `/opt/radar/.venv/bin/python /opt/radar/main.py` manualmente quando voltar. Não use este crontab do host junto com o cron já ativo no serviço Docker.

No Windows, crie uma tarefa no **Agendador de Tarefas** para iniciar de segunda a sexta às 21h30. Em **Ação**, escolha “Iniciar um programa”, informe o caminho completo para `python.exe` do ambiente virtual, por exemplo `C:\Radar Ações\.venv\Scripts\python.exe`, e em **Adicionar argumentos** informe `main.py`. Em **Iniciar em**, informe a pasta do clone. Na guia **Configurações**, habilite a execução assim que possível após um início agendado perdido. Para registrar a saída, use um arquivo `.bat` como ação:

```bat
@echo off
cd /d "C:\Radar Ações"
"C:\Radar Ações\.venv\Scripts\python.exe" main.py >> logs\cron.log 2>&1
```

Confira `logs\radar.log` e `logs\cron.log`. No Windows, marque a opção de executar a tarefa após o horário perdido. No macOS/Linux, rode `python main.py` manualmente se o computador estava desligado no horário.

## Limites e revisão

- O COTAHIST é filtrado pelos códigos de mercado e lote padrão usados no roteiro de referência. Dados ausentes, formato alterado ou desdobramentos podem exigir revisão.
- O universo dos EUA deriva das listas `nasdaqlisted` e `otherlisted` da Nasdaq Trader; o nome da listagem é usado para excluir tipos de instrumento que não sejam ações comuns. Nomes empresariais para RSS vêm dos CSVs versionados em `referencias/`.
- RSS pode conter resultados irrelevantes, duplicados ou incompletos; a contagem é uma aproximação de atenção.
- Pesos e percentis são parâmetros de pesquisa, não evidência de desempenho futuro.
- Verifique os CSVs e as primeiras watchlists antes de confiar nos resultados. A lista não é recomendação de investimento.

Para conferir a atenção por busca, use o [Google Trends](https://trends.google.com/trends/) manualmente nas 10 maiores notas. O Radar não usa bibliotecas não oficiais do Trends; consulte a [documentação oficial do Google Trends](https://developers.google.com/search/apis/trends) para o status e as condições atuais de acesso à API.

## Fontes, pesquisa e enquadramento

- Cotações B3: [histórico oficial COTAHIST](https://www.b3.com.br/pt_br/market-data-e-indices/servicos-de-dados/market-data/historico/mercado-a-vista/cotacoes-historicas/).
- Universo dos EUA: [Nasdaq Listed](https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt) e [Other Listed](https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt); preços via [yfinance](https://github.com/ranaroussi/yfinance), sujeito à disponibilidade e aos termos do Yahoo Finance.
- Manchetes: RSS de busca do [Google Notícias](https://news.google.com/).
- Estudos citados no material: [Gervais, Kaniel e Mingelgrin](https://doi.org/10.1111/0022-1082.00349), [Da, Engelberg e Gao](https://doi.org/10.1111/j.1540-6261.2011.01679.x), [Barber e Odean](https://doi.org/10.1093/rfs/hhm079) e [Barber, Huang, Odean e Schwarz](https://doi.org/10.1111/jofi.13183). A evidência descrita no PDF inclui alta de curto prazo e reversão, além de resultados adversos associados a compras por atenção; os pesos do Radar não são calibrados por esses estudos.
- Regras brasileiras citadas no material: [Resolução CVM 20](https://conteudo.cvm.gov.br/legislacao/resolucoes/resol020.html) e [Resolução CVM 19](https://conteudo.cvm.gov.br/legislacao/resolucoes/resol019.html). O Radar se destina a estudo pessoal e não produz recomendação ou aconselhamento profissional.
