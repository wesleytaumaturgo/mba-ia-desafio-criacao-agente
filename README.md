# Regra é regra: o assistente virtual do Residencial Aurora

API em Python (FastAPI + Google ADK 2.9.1) com um assistente para os moradores do Residencial Aurora: reservar áreas comuns, cancelar reservas, autorizar visitantes e tirar dúvidas do regulamento. O modelo conduz a conversa; o código decide o que é permitido.

## Arquitetura

Um agente principal e três especialistas, montados em `aurora/agentes.py:24` (`criar_app`). O modelo entra por fábrica, então os testes usam um LLM roteirizado e nunca chamam a rede.

| Agente | Responsabilidade | Como é acionado | Por quê |
|---|---|---|---|
| `principal` | Atende o morador e distribui o trabalho. Não tem o regulamento nas instruções. | Raiz do app | Um ponto de entrada só; o especialista certo vem da intenção da mensagem. |
| `reservas` | Disponibilidade, reservar, cancelar e listar reservas. | Transferência (`sub_agents`) | A conversa de reserva tem vários turnos e a confirmação precisa voltar ao agente que a pediu. |
| `visitantes` | Listar e autorizar visitantes. | Transferência (`sub_agents`) | Mesmo motivo: a autorização pede confirmação e retoma no mesmo agente. |
| `regulamento` | Responde dúvidas com o capítulo devolvido por `consultar_regulamento`. | `AgentTool` | Pergunta e resposta isoladas: os eventos internos do especialista não entram na sessão principal. |

Armazenamento: `var/condominio.db` (reservas e visitantes) e `var/sessoes.db` (sessões do ADK, `SqliteSessionService`), na pasta de `AURORA_VAR_DIR`. Apartamentos, áreas e regulamento são lidos de `dados/`, somente leitura.

## Garantias

Cada garantia está no código e não depende do que o modelo decide.

1. **Cobrança ou acesso só com confirmação.** `aurora/tools.py:81` (`require_confirmation=precisa_confirmar`) pede confirmação ao reservar área com taxa em data livre, e `aurora/tools.py:84` (`require_confirmation=True`) pede sempre ao autorizar visitante. A pendência é derivada dos eventos em `aurora/api.py:54` (`def pendencias`), e a rota de confirmações só aceita um `id` pendente: `aurora/api.py:150` (`pedido_id not in`) devolve 409 sem rodar o Runner. Dizer "já confirmei" na conversa não muda nada.
2. **Cada sessão pertence a um apartamento.** O apartamento nasce em `aurora/api.py:113` (`state={"apartamento": apartamento}`) e as tools o leem do estado em `aurora/tools.py:12` (`tool_context.state["apartamento"]`). Nenhuma tool tem parâmetro de apartamento, e o retorno das tools nunca traz código ou unidade de outro morador.
3. **Nada se perde no reinício.** As sessões ficam em SQLite, abertas em `aurora/api.py:104` (`SqliteSessionService`), e as pendências vêm dos eventos gravados. Reservas e visitantes estão em `var/condominio.db`, que a subida não apaga.
4. **O regulamento é consultado, não carregado.** `aurora/regulamento.py:67` (`def consultar_regulamento`) devolve um único capítulo escolhido por código. A tool só existe no especialista `regulamento`, acionado como `AgentTool` em `aurora/agentes.py:77` (`AgentTool(agent=reg)`).
5. **Dois moradores, uma reserva.** O índice único parcial em `aurora/dados.py:26` (`ux_reserva_ativa`) vale no instante da gravação. `aurora/dados.py:125` (`data ocupada`) transforma a violação em resposta normal, `reservado: false`, e não em erro de servidor.

O código da reserva (`RSV-` + 8 hex) vem de `aurora/dados.py:103` (`secrets.token_hex`) e nunca volta, porque cancelar só marca `ativa = 0`.

## Como rodar

Pré-requisitos: Python 3.12 ou superior e [uv](https://docs.astral.sh/uv/).

1. Instale as dependências: `uv sync`.
2. Copie o modelo de variáveis e preencha só a chave: `cp .env.example .env`.

   | Variável | Uso |
   |---|---|
   | `GOOGLE_API_KEY` | Chave do Google AI Studio (obrigatória; sem ela a API não sobe). |
   | `AURORA_MODELO` | Modelo Gemini (opcional; padrão em `aurora/modelo.py`). |
   | `AURORA_VAR_DIR` | Pasta dos bancos SQLite (opcional; padrão `var`). |

3. Restaure os dados iniciais (apaga reservas, visitantes e sessões):

   ```
   uv run python -m aurora.restaurar
   ```

4. Suba a API em `http://localhost:8000` (a subida não restaura nada):

   ```
   uv run uvicorn aurora.api:app --port 8000
   ```

Testes: `uv run pytest -q`. O fluxo do avaliador completo, com o Gemini real, roda com `bash scripts/fluxo-avaliador.sh [pasta]` e grava `passo-NN.txt` na pasta.
