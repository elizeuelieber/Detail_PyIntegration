# Quallity Data Integration — Docker

Projeto preparado para executar a coleta Selenium e a importação do backup SQL em um serviço Python separado do n8n. Inclui um painel web em português e uma API HTTP para automações.

## Pré-requisitos

- VPS com Docker Engine e Docker Compose v2.
- O projeto disponível no VPS.
- Acesso ao site AMW Systems e ao banco MySQL.
- O serviço MySQL deve aceitar conexões originadas do contêiner. Se o banco estiver em outro contêiner, conecte-o à rede compartilhada ou use um hostname acessível nessa rede.

## Configuração inicial

1. Copie `.env.example` para `.env` e defina um token longo e aleatório em `API_TOKEN`. Não compartilhe esse segredo.
2. Edite `config/config.json`: preencha credenciais do site e do MySQL. Os diretórios `/data/downloads` e `/data/logs` já estão configurados e persistem na pasta `data/`.
3. Se MySQL estiver no próprio VPS, `host.docker.internal` pode não resolver em Linux por padrão. Substitua pelo IP privado/hostname do VPS ou pelo nome do contêiner na rede Docker. Não use `localhost` para um serviço que roda fora deste contêiner.
4. Crie a rede compartilhada uma única vez:

   ```bash
   docker network create quallity-net
   ```

5. Na configuração Docker Compose do n8n, adicione a rede ao serviço `n8n` e declare-a como externa no final do arquivo:

   ```yaml
   services:
     n8n:
       # mantenha as configurações que já possui
       networks:
         - quallity-net

   networks:
     quallity-net:
       external: true
       name: quallity-net
   ```

   Se o serviço `n8n` já lista outra rede, mantenha-a e acrescente `quallity-net`. Recrie o n8n para aplicar a mudança.

6. No diretório deste projeto, construa e inicie o serviço:

   ```bash
   docker compose up -d --build
   docker compose ps
   ```

## Painel visual

A porta padrão é `8088`, vinculada somente a `127.0.0.1` no VPS. Acesse com um túnel SSH local:

```bash
ssh -L 8088:127.0.0.1:8088 usuario@IP_DO_VPS
```

Depois abra `http://localhost:8088` no navegador e informe o mesmo `API_TOKEN` do `.env`. Não exponha a porta diretamente à internet; para uso público, configure autenticação e TLS no proxy reverso.

## Configurar no n8n

Use um nó **HTTP Request** apontando para o hostname Docker `http://python-crawler:8000` (não use `localhost` dentro do n8n):

- Iniciar o fluxo completo: `POST /api/run`, JSON `{"stage":"all"}`.
- Somente coletar: `POST /api/run`, JSON `{"stage":"crawl"}`.
- Somente importar: `POST /api/run`, JSON `{"stage":"import"}`.
- Consultar andamento: `GET /api/status`.

Envie o cabeçalho `X-API-Token` com o valor de `API_TOKEN`. A chamada de início responde com um `job_id`; consulte o estado periodicamente até `running` ser `false`. Uma nova execução enquanto outra estiver ativa retorna HTTP 409. A documentação interativa da API está em `/api/docs`.

## Pastas persistentes

- `data/downloads/`: backups SQL obtidos pelo Selenium.
- `data/logs/`: registros JSON e erros da importação.

O arquivo de configuração fica separado do código e é montado somente para leitura no serviço. Não publique `config/config.json`, `.env` ou os backups em repositórios públicos.

## Observações antes da primeira execução

- O fluxo completo **baixa e importa** o backup no MySQL configurado; confirme o banco de destino e faça um backup de segurança antes de testar.
- A lógica de navegação usa seletores do site fornecido e pode precisar de ajustes se a página mudar. A autenticação e os downloads reais dependem de acesso ao site.
- O importador lê as diretivas `DELIMITER` do dump e separa instruções respeitando strings, identificadores entre crases, comentários e blocos de procedures. Ainda assim, valide primeiro em uma cópia/banco de teste: falhas podem deixar alterações parciais porque comandos DDL do MySQL fazem commit implícito.
- A imagem instala Chromium/driver do repositório Debian no build. Se o build falhar por disponibilidade de pacotes ou arquitetura do VPS, veja o erro do Docker; o requisito é imagem Debian compatível com a arquitetura suportada.
- A API roda uma execução por vez em memória. Ao recriar o contêiner durante um job, o job é interrompido; os arquivos baixados e logs permanecem na pasta persistente.

## Operação

```bash
docker compose logs -f python-crawler
docker compose restart python-crawler
docker compose down
```

`docker compose down` remove o contêiner, mas mantém os dados no diretório `data/`.
