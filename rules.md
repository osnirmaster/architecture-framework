# Regras Normativas do Corporate Architecture Twin

As palavras **DEVE**, **NÃO DEVE**, **PODE** e **DEVERIA** são normativas.

## 1. Princípios centrais

1. O LLM **DEVE propor**; o harness determinístico **DEVE decidir** se a proposta pode ser publicada.
2. Nenhum agente **DEVE escrever diretamente** no grafo publicado.
3. Toda afirmação **DEVE apontar para evidência verificável**.
4. Ausência de evidência **NÃO DEVE ser convertida em evidência de ausência**.
5. O sistema **DEVE preferir abstenção explícita** a uma classificação plausível sem suporte.
6. O grafo **DEVE ser reconstruível** a partir dos repositórios, catálogo, configuração e propostas aceitas.

## 2. Contratos entre camadas

### Layer 1 — Evidence

Entrada: arquivos de repositórios em um commit conhecido.

Saída: `workspace/evidence/<repository>/<commit>/.../*.json`.

A evidência **DEVE** ser observacional e não interpretativa. Exemplos:

- dependência declarada em manifest;
- endpoint encontrado em OpenAPI ou annotation;
- evento publicado ou consumido;
- recurso IaC;
- tabela, coluna ou entidade encontrada em DDL;
- métrica, dashboard, trace ou configuração de APM;
- ADR, diagrama, README ou runbook;
- job, stage ou deployment encontrado em pipeline.

### Layer 2 — LLM Wiki Compiler

Entrada: evidências.

Saída: `workspace/claims/**/*.json`.

Um claim **DEVE** declarar:

- `claimType`;
- `statement` estruturado;
- `evidenceRefs`;
- `epistemicStatus`: `EXPLICIT`, `INFERRED`, `AMBIGUOUS` ou `CONFLICTED`;
- `confidence` entre 0 e 1;
- agente e versão do prompt;
- alternativas consideradas quando aplicável.

### Layer 3 — Semantic Modeling

Entrada: claims, catálogo LeanIX e metamodelo.

Saída: `workspace/proposals/nodes/*.json` e `workspace/proposals/relationships/*.json`.

A camada semântica **DEVE** mapear realidade técnica para conceitos corporativos sem criar factsheets fictícios. Quando não houver correspondência segura, a proposta **DEVE** usar `leanix.matchStatus = "UNMATCHED"` e ser enviada à quarentena.

### Deterministic Harness

Entrada: propostas semânticas.

Saída:

- `workspace/harness/accepted.json`;
- `workspace/harness/quarantine.json`;
- `workspace/harness/rejected.json`;
- `workspace/harness/diagnostics.json`;
- após publicação: `dist/graph/graph.json` e `dist/graph/diff.json`.

### Layer 4 — Corporate Graph Digital Twin

A Layer 4 **DEVE conter somente** nós e relações aceitos pelo harness. Evidências e claims ficam fora do grafo corporativo e são referenciados por provenance.

## 3. Metamodelo corporativo

Todo nó publicado **DEVE** possuir um `type` permitido em `config/metamodel.json`.

Tipos principais:

- `Capability`
- `BusinessService`
- `ApplicationService`
- `Application`
- `ApplicationComponent`
- `TechCategory`
- `Infrastructure`
- `DataDomain`
- `CanonicalDataEntity`
- `Document`
- `ArchitectureState`
- `Product`
- `ValueStream`
- `FitnessFunction`
- `FitnessViolation`
- `ArchitectureWaiver`

`ApplicationService` **DEVERIA** usar subtipos como `API`, `EVENT`, `BATCH`, `UI`, `DOMAIN_SERVICE` ou `INTEGRATION_SERVICE`.

Tipos que ainda não existam como factsheet LeanIX **DEVEM** declarar `metamodelOrigin = "TWIN_EXTENSION"` e uma estratégia de mapeamento corporativo explícita.

## 4. Identidade e nomenclatura

### Identificador estável

Formato obrigatório:

```text
urn:corp-arch:<type-kebab>:<namespace-kebab>:<canonical-name-kebab>
```

Exemplo:

```text
urn:corp-arch:application:payments:payment-orchestrator
```

Regras:

- IDs **NÃO DEVEM** conter branch, commit, ambiente ou nome de arquivo.
- Mudança de nome de exibição **NÃO DEVE** alterar o ID sem uma decisão explícita de identidade.
- `canonicalName` **DEVE** ser `kebab-case`, sem acentos e semanticamente estável.
- `name` **DEVE** ser legível por humanos e seguir o catálogo quando existir factsheet correspondente.
- aliases históricos **DEVEM** ficar em `aliases`.

### Descrição

A descrição **DEVE** explicar responsabilidade e fronteira, não tecnologia incidental.

Modelo recomendado:

```text
Responsável por <resultado/capacidade>, atendendo <atores/contexto>, por meio de <fronteira funcional>. Não é responsável por <exclusões relevantes>.
```

## 5. Campos mínimos do nó

Todo nó publicado **DEVE** conter:

- `id`
- `type`
- `name`
- `canonicalName`
- `description`
- `metamodelOrigin`
- `lifecycle`
- `architectureState`
- `provenance`
- `inference`
- `attributes`

Metadados recomendados:

- `namespace`
- `subtype`
- `owners`
- `businessDomain`
- `tags`
- `aliases`
- `leanix.factSheetId`
- `observedAt`
- `validFrom`
- `validTo`
- `repositoryRefs`

## 6. Provenance

Cada evidência referenciada **DEVE** identificar, quando aplicável:

- repositório;
- commit SHA;
- caminho do arquivo;
- intervalo de linhas ou seletor estrutural;
- hash do conteúdo;
- extractor ou agente responsável;
- timestamp da observação.

Uma proposta `INFERRED` **DEVE** possuir ao menos duas evidências independentes ou ser colocada em quarentena. Duas referências ao mesmo trecho não contam como independentes.

## 7. Relações

Relações **DEVEM** usar apenas tipos permitidos em `config/relationship-policy.json`.

Relações principais:

- `DECOMPOSES_TO`
- `SUPPORTS`
- `REQUIRES`
- `REALIZES`
- `PROVIDES`
- `CONSUMES`
- `PART_OF`
- `IMPLEMENTS`
- `DEPENDS_ON`
- `CALLS`
- `USES`
- `HOSTS`
- `READS`
- `WRITES`
- `BELONGS_TO`
- `DESCRIBES`
- `APPLIES_TO`
- `ENABLES`
- `AFFECTS`

Toda relação **DEVE** ter direção, endpoints existentes, descrição curta, provenance e estado epistêmico.

## 8. Política de confiança e abstenção

Confiança numérica é auxiliar e **NÃO DEVE** ser o único critério de publicação.

- `EXPLICIT`: pode ser aceito com uma evidência direta válida.
- `INFERRED`: requer evidências independentes e `confidence >= 0.85`.
- `AMBIGUOUS`: sempre vai para quarentena.
- `CONFLICTED`: bloqueado até resolução ou política explícita de coexistência temporal.

Mapeamentos para `Capability`, `BusinessService`, `Product` e `ValueStream` **NÃO DEVEM criar automaticamente** novos fatos corporativos no MVP. Eles podem apenas:

1. referenciar um factsheet existente;
2. propor um candidato em quarentena;
3. registrar que não houve correspondência.

## 9. Conflitos

O conflict detector **DEVE** distinguir:

- conflito temporal: versões ou estados distintos ao longo do tempo;
- conflito ambiental: comportamentos diferentes por ambiente;
- conflito documental: documentação diverge do código;
- conflito de identidade: dois nomes parecem representar a mesma entidade;
- conflito de classificação: uma entidade possui tipos corporativos incompatíveis;
- conflito de ownership: proprietários divergentes.

Código e IaC prevalecem para realidade implantável; catálogo LeanIX prevalece para identidade corporativa; documentação não pode sobrescrever implementação sem confirmação.

## 10. Gates determinísticos

### BLOCK

- JSON inválido;
- tipo de nó desconhecido;
- relação não permitida;
- endpoint inexistente;
- ID duplicado com conteúdo incompatível;
- ausência de provenance;
- tentativa de editar `dist/graph/**` manualmente;
- secrets ou credenciais detectados;
- claim `CONFLICTED` publicado como fato;
- referência LeanIX para factsheet inexistente no catálogo.

### QUARANTINE

- confiança insuficiente;
- classificação ambígua;
- descrição vaga;
- possível duplicidade;
- mapeamento corporativo sem factsheet confirmado;
- inferência suportada por uma única evidência indireta;
- mudança destrutiva de identidade.

### WARN

- nó sem owner;
- nó sem domínio;
- descrição curta;
- ausência de estado futuro;
- possível órfão em uma visão específica.

## 11. Segurança

- Segredos, tokens, chaves, dados pessoais e payloads sensíveis **NÃO DEVEM** ser copiados para evidências ou grafo.
- O extractor **DEVE** mascarar valores e preservar apenas metadados necessários.
- Arquivos `.env`, secrets Kubernetes e stores de credenciais **NÃO DEVEM** ser ingeridos como conteúdo.
- O grafo **DEVE** representar a existência e o tipo de segredo, nunca seu valor.

## 12. Arquivos gerados e governados

- `catalog/**`: governado externamente; não alterar por inferência.
- `workspace/**`: efêmero, auditável e recriável.
- `dist/**`: gerado exclusivamente pelo harness.
- `config/**`: governado por arquitetura; alterações exigem justificativa e testes.
- `schemas/**`: contratos de integração; mudanças incompatíveis exigem incremento de versão.

## 13. Architectural fitness functions

1. Toda fitness function **DEVE** ser declarada em `fitness/definitions/` e validada contra `schemas/fitness/fitness-function.schema.json`.
2. A definição **DEVE** separar `target`, `evaluation.phase`, `evaluation.evaluatorType`, `policy` e `result`.
3. O LLM **NÃO DEVE** decidir enforcement. A engine produz avaliações; o harness aplica `BLOCK`, `QUARANTINE`, `WARN`, `ADVISORY` ou `OBSERVE`.
4. `UNKNOWN` e `STALE` **NÃO DEVEM** ser convertidos em `PASS`. A regra **DEVE** declarar `onUnknown` ou aceitar a política global.
5. `LLM_ASSISTED` **NÃO DEVE** usar `BLOCK` sem uma confirmação determinística ou humana separada.
6. Fitness functions de fase `EVIDENCE` **DEVEM** registrar arquivos ou evidências que sustentam o resultado.
7. Fitness functions de fase `RUNTIME` **DEVEM** avaliar freshness e distinguir telemetria ausente de threshold atendido.
8. Funções `COMPOSITE` **DEVEM** referenciar IDs estáveis e não podem formar ciclos de composição.
9. Uma mudança incompatível no significado de uma regra **DEVE** incrementar sua versão e, quando necessário, usar um novo ID.
10. Resultados **DEVEM** usar somente: `PASS`, `FAIL`, `UNKNOWN`, `NOT_APPLICABLE`, `ERROR`, `STALE` ou `WAIVED`.

## 14. Waivers arquiteturais

1. Waivers **DEVEM** ficar em `fitness/waivers/` e validar contra `schemas/fitness/architecture-waiver.schema.json`.
2. Todo waiver **DEVE** possuir função, alvo, justificativa, aprovador, início e expiração explícitos.
3. Waivers **DEVERIAM** referenciar ADR e ticket de remediação.
4. Waiver expirado, futuro ou fora do escopo **NÃO DEVE** alterar o resultado.
5. Um waiver **NÃO DEVE** apagar a violação; o estado deve ser `WAIVED`, preservando o resultado original.
6. Agentes **NÃO DEVEM** aprovar waivers ou estender validade por inferência.

## 15. Materialização de conformidade

- `FitnessFunction`, `FitnessViolation` e `ArchitectureWaiver` são tipos `TWIN_EXTENSION`.
- A engine **PODE** materializar `APPLIES_TO`, `INSTANCE_OF`, `AFFECTS` e `WAIVES`.
- Violações cujo alvo foi rejeitado ou colocado em quarentena **NÃO DEVEM** criar relações órfãs no snapshot publicado.
- Histórico completo de execuções fica em arquivos de resultado; o grafo publicado representa a condição corrente.
