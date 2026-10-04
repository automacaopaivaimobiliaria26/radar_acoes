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
├── dashboard.py        # página visual que lê as watchlists Markdown
└── requirements.txt
```

Os arquivos de dados, logs e watchlists ficam fora do controle de versão por padrão. `b3.csv` e `eua.csv` têm as colunas `data`, `ticker`, `fechamento`, `quantidade` e volume financeiro (`volume_rs` na B3; `volume` nos EUA). Os arquivos `nomes_b3.csv` e `nomes_eua.csv` alimentam as consultas com nome empresarial. As descrições curtas vêm do perfil público do Yahoo Finance e ficam em cache local em `dados/empresas.csv`.

## Instalação no Ubuntu

```bash
cd "/caminho/para/Radar Ações"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

O coletor dos EUA usa `yfinance`, uma biblioteca não oficial que lê dados do Yahoo Finance, para pesquisa pessoal. Disponibilidade, limites e termos de uso podem mudar. O coletor repete lotes e só substitui o CSV de produção quando a coleta termina com dados; se um lote falhar, preserva o CSV anterior. A lista do Nasdaq Trader é filtrada para Nasdaq, NYSE e NYSE American, removendo ETFs, títulos de teste e instrumentos que não pareçam ações comuns.

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
4. Calcula volume relativo, retornos de 5 e 20 pregões, percentis separados por mercado e a nota ponderada. Na B3, BDRs e ações com salto absoluto superior a 40% em uma das últimas 20 variações são excluídos para reduzir distorções de eventos corporativos não ajustados.
5. Salva `saida/watchlist_AAAA-MM-DD.md` e registra a execução em `logs/radar.log`.

Se a atualização de um mercado falhar, o Radar tenta gerar uma watchlist com o histórico daquele mercado já existente e o mercado que conseguiu atualizar. Se a etapa de notícias falhar, gera a watchlist com os componentes disponíveis. A execução fica registrada como incompleta (código diferente de zero e sem atualizar `ultimo_sucesso.txt`); confira o log antes de considerar a saída completa. A ausência de um dos CSVs de cotações omite esse mercado em vez de interromper o outro.

`python main.py --limite-eua 20` executa apenas uma coleta de teste dos EUA e grava em `dados/eua_teste.csv` e `dados/nomes_eua_teste.csv`. Não atualiza os dados de produção nem gera watchlist. O mesmo vale para `python eua.py --limite 20`; para outro destino de teste, informe `--saida` e `--nomes-saida`. Uma coleta com limite não pode sobrescrever `dados/eua.csv` ou `dados/nomes_eua.csv`.

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

Quando a atenção está ausente ou parcial, o cálculo redistribui proporcionalmente os pesos dos componentes disponíveis e marca a nota como parcial. A seleção de notícias consulta ticker, nome empresarial e a descrição curta disponível; sem nome ou descrição, usa os dados que existirem. As descrições e bolsas ficam em cache por até 180 dias; falhas de perfil são tentadas novamente após 14 dias e não interrompem a coleta. A watchlist inclui ticker, empresa, bolsa e descrição, exibindo “Não informada” ou “Descrição indisponível” quando a fonte não fornecer esses dados. Os percentis são calculados dentro de cada mercado e com dados disponíveis até a data da watchlist.

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

O dashboard lista os arquivos `watchlist_AAAA-MM-DD.md` em `/app/saida`, abre por padrão o mais recente e permite selecionar datas anteriores. Os cartões e tabelas são montados diretamente a partir das tabelas Markdown, sem alterar os arquivos gerados pelo Radar.

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
