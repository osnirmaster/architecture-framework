# Catálogo de Agentes — Corporate Architecture Twin

Este arquivo define a topologia lógica. As definições executáveis ficam em `.claude/agents/*.md`.

## Convenções comuns

Todos os agentes:

- obedecem `CLAUDE.md` e `rules.md`;
- retornam dados estruturados, não apenas narrativa;
- citam repositório, commit, caminho e linhas;
- registram `UNKNOWN` em vez de completar lacunas;
- nunca publicam diretamente em `dist/graph`;
- não alteram o catálogo LeanIX;
- separam fatos observados de inferências.

## Supervisor

### `twin-supervisor`

Responsável por decompor a execução por repositório e camada, controlar orçamento, evitar trabalho duplicado e garantir que o harness seja executado. Pode delegar apenas a orquestradores de camada e ao revisor final.

## Layer 1 — Evidence Layer

### `evidence-orchestrator`

Coordena coleta incremental por commit. Produz um manifest de execução e consolida evidências sem interpretação semântica.

Subagentes:

- `code-cartographer`: módulos, dependências, endpoints, eventos, consumers, chamadas e objetivo técnico observável.
- `infra-cartographer`: Terraform, CloudFormation, CDK, Kubernetes, Helm, Docker e topologia de deployment.
- `data-cartographer`: DDL, migrations, entidades, schemas, tabelas, tópicos e catálogos.
- `runtime-cartographer`: observabilidade, APM, métricas, traces, SLOs, health checks e configurações de runtime.
- `documentation-cartographer`: ADRs, diagramas, READMEs, processos, runbooks e arquitetura documentada.
- `pipeline-cartographer`: CI/CD, stages, artefatos, ambientes, controles e estratégias de rollout.

Contrato de saída: `workspace/evidence/<repo>/<commit>/<cartographer>/*.json`.

## Layer 2 — LLM Wiki Compiler

### `wiki-compiler-orchestrator`

Compila evidências em claims atômicos, deduplicados e interligados. Mantém o conhecimento acumulativo, mas nunca promove claim para fato corporativo.

Subagentes:

- `relationship-inference`: propõe relações técnicas e semânticas com justificativa.
- `entity-classifier`: classifica candidatos no metamodelo sem resolver identidade.
- `entity-mapper`: resolve candidatos para entidades já conhecidas ou registra ausência de match.
- `purpose-inference`: infere responsabilidade, fronteira e objetivo com exclusões explícitas.
- `conflict-detector`: identifica divergências entre fontes, versões e ambientes.
- `abstention-controller`: revisa claims frágeis e rebaixa `INFERRED` para `AMBIGUOUS` quando necessário.

Contrato de saída: `workspace/claims/**/*.json`.

## Layer 3 — Semantic Modeling Layer

### `semantic-modeling-orchestrator`

Conecta implementação ao negócio usando o catálogo corporativo. Produz somente propostas de nós e relações.

Subagentes:

- `business-mapper`: BusinessService, Product, ValueStream e contexto de negócio.
- `capability-mapper`: Capability e decomposição de capacidades.
- `domain-mapper`: DataDomain, domínios funcionais e entidades canônicas.
- `duplication-detector`: duplicidades, aliases e sobreposição de responsabilidade.
- `impact-analyser`: impacto upstream/downstream e blast radius de mudanças.
- `semantic-validator`: coerência do metamodelo antes do harness determinístico.

Contrato de saída:

- `workspace/proposals/nodes/*.json`
- `workspace/proposals/relationships/*.json`

## Gate — Deterministic Harness

O harness **não é um agente LLM**. É código determinístico executado por `scripts/harness.py`.

Responsabilidades:

1. validar schemas;
2. validar IDs e nomenclatura;
3. validar tipos e cardinalidades;
4. conferir referências LeanIX;
5. verificar provenance;
6. aplicar política de confiança;
7. detectar duplicidades e ciclos proibidos;
8. classificar cada proposta em `accepted`, `quarantine` ou `rejected`;
9. gerar diff reproduzível;
10. publicar snapshot imutável.

## Layer 4 — Corporate Graph Digital Twin

### `graph-view-orchestrator`

Trabalha somente sobre o snapshot aceito. Cria especificações de visualização e análises, nunca altera fatos do grafo.

Subagente:

- `graph-view-builder`: produz filtros, agrupamentos, layouts e métricas para as visões estratégica, capacidades, serviços, aplicações, infraestrutura, dados e roadmap.

Visões mínimas:

- estratégica;
- capacidades;
- serviços;
- aplicações;
- infraestrutura;
- dados;
- roadmap.

## Sequência recomendada de execução

```text
@twin-supervisor
  -> @evidence-orchestrator
       -> cartographers em paralelo
  -> @wiki-compiler-orchestrator
       -> compiler agents
  -> @semantic-modeling-orchestrator
       -> semantic agents
  -> python3 scripts/harness.py validate
  -> revisão de quarantine/rejected
  -> python3 scripts/harness.py publish
  -> @graph-view-orchestrator
```

## Política de paralelismo

- Cartographers podem rodar em paralelo por repositório e por tipo de evidência.
- Inferência, classificação e purpose podem rodar em paralelo após consolidação de evidências.
- Conflict detector e abstention controller devem rodar depois dos demais compiler agents.
- Semantic validator deve rodar por último na Layer 3.
- Publicação é serial e protegida pelo harness.

## Layer 3.5 — Architecture Fitness

### `fitness-orchestrator`

Coordena a seleção de regras por fase, valida o DSL, executa a engine e entrega resultados ao harness. Não possui autoridade para publicar nem para aprovar exceções.

Subagentes:

- `fitness-dsl-author`: cria e revisa regras compatíveis com o schema;
- `semantic-fitness-evaluator`: metamodelo, ownership, lifecycle e alinhamento corporativo;
- `graph-fitness-evaluator`: cardinalidade, relações proibidas e ciclos;
- `evidence-fitness-evaluator`: código, IaC, contratos e pipelines;
- `runtime-fitness-evaluator`: métricas, thresholds e freshness;
- `waiver-validator`: contrato, escopo, aprovação e validade de exceções;
- `fitness-conflict-detector`: redundância, contradições e composições circulares.

Contrato de saída: `workspace/harness/fitness-results.json`.

A sequência atualizada é:

```text
@semantic-modeling-orchestrator
  -> @fitness-orchestrator
  -> python3 scripts/harness.py validate
  -> revisão de quarantine/rejected/waived
  -> python3 scripts/harness.py publish
```
