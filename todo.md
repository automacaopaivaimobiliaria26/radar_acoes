# Radar de Ações — dashboard web

## Plano de implementação

- [x] Ler as instruções do `construtor-site` e revisar Compose, Dockerfile, branch e remoto.
- [x] Registrar que a página oficial já usa `www.paivanegociosimobiliarios.com.br`; evitar substituir a raiz do site.
- [x] Criar o dashboard para listar e visualizar watchlists Markdown por data.
- [x] Compartilhar `radar_saida` com o dashboard em modo somente leitura e manter o coletor cron sem porta pública.
- [x] Documentar o Basic Auth no EasyPanel e o roteamento web separado da página principal.
- [x] Criar validações automatizadas para parser, lista de datas e respostas HTTP.
- [x] Executar os testes Python e corrigir falhas; Compose YAML, shell e diff também validados.
- [x] Preparar Compose de homologação isolado com fixture, sem cron nem acesso aos volumes de produção.
- [x] Registrar o endereço de produção confirmado: `https://www.paivanegociosimobiliarios.com.br/radar`.
- [ ] Executar checagem de tipos e build Docker (não disponíveis neste computador; `mypy` e Docker não estão instalados).
- [ ] Enviar a tag de preservação ao GitHub; requer rede e sessão autenticada.
- [ ] Publicar a branch; a tentativa de verificar o remoto foi bloqueada após rejeição da autorização de rede.
- [ ] Validar visualmente a homologação no EasyPanel com o domínio temporário e Basic Auth.
- [ ] Publicar no domínio oficial após validar o deploy e a proteção por senha; o usuário autorizou a publicação direta.
