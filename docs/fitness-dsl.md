# Referência do Fitness Function DSL

## Documento mínimo

```yaml
apiVersion: architecture.company/v1
kind: FitnessFunction
metadata:
  id: FF-DOMAIN-001
  name: Nome legível
  description: Responsabilidade e intenção arquitetural da regra.
  owner: architecture-team
  version: 1.0.0
spec:
  target:
    nodeTypes: [Application]
    selectors:
      fields: {}
      attributes: {}
  evaluation:
    phase: GRAPH
    evaluatorType: GRAPH_QUERY
    requiredEvidence: []
    query: {}
  policy:
    severity: HIGH
    enforcement: QUARANTINE
    onUnknown: QUARANTINE
    appliesTo:
      lifecycle: [ACTIVE]
      architectureState: [CURRENT, TARGET]
  result:
    violationType: DOMAIN_BOUNDARY
    remediation:
      description: Descreva uma ação concreta para resolver a violação.
```

## Selectors

Conditions aceitas em `fields` e `attributes`:

```yaml
selectors:
  fields:
    lifecycle: {in: [ACTIVE, PLANNED]}
    owners: {exists: true}
  attributes:
    exposure: {equals: PUBLIC}
    criticality: {notIn: [LOW]}
    availabilityTarget: {gte: 99.9}
    technology: {regex: "(?i)java|go"}
```

Operadores: `equals`, `notEquals`, `in`, `notIn`, `exists`, `regex`, `contains`, `gte`, `lte`.

## GRAPH_QUERY

### Cardinalidade

```yaml
evaluation:
  phase: GRAPH
  evaluatorType: GRAPH_QUERY
  query:
    mode: relationshipCardinality
    relationship: SUPPORTS
    direction: OUT
    targetNodeTypes: [Capability, BusinessService]
    minimum: 1
    maximum: 5
```

### Relação proibida

```yaml
query:
  mode: forbiddenRelationship
  relationshipTypes: [WRITES]
  direction: OUT
  targetNodeTypes: [CanonicalDataEntity]
```

### Ciclos

```yaml
query:
  mode: acyclic
  relationshipTypes: [DEPENDS_ON, CALLS]
```

## SEMANTIC_ASSERTION

```yaml
query:
  requiredFields: [owners, businessDomain]
  requiredAttributes: [criticality]
  assertions:
    - path: architectureState
      operator: notEquals
      value: UNKNOWN
```

## FILE_QUERY e IAC_POLICY

O nó precisa declarar `repositoryRefs`, e `config/repositories.json` precisa resolver o clone local quando o ref não contém `localPath`.

```yaml
query:
  includeGlobs: ["**/Dockerfile"]
  excludeGlobs: ["**/vendor/**"]
  mustExist: true
  requireRegex: ["(?im)^USER\\s+[^0]"]
  forbidRegex: ["(?im)^FROM\\s+.+:latest"]
  fileMode: ALL
```

## SCHEMA_QUERY

```yaml
query:
  includeGlobs: ["**/openapi.yaml"]
  documentMode: ANY
  assertions:
    - path: components.securitySchemes.oauth2.type
      operator: equals
      value: oauth2
```

## RUNTIME_THRESHOLD

Arquivo de evidência esperado:

```json
{
  "targetId": "urn:corp-arch:application-service:payments:authorize-payment",
  "observedAt": "2026-08-02T20:00:00Z",
  "metrics": {"latencyP95Ms": 240}
}
```

Regra:

```yaml
query:
  metricsGlob: workspace/evidence/**/runtime/**/*.json
  targetIdPath: targetId
  metricPath: metrics.latencyP95Ms
  observedAtPath: observedAt
  operator: lte
  threshold: 300
  maxAgeHours: 24
```

Operadores numéricos: `gt`, `gte`, `lt`, `lte`, `eq`, `ne`.

## COMPOSITE

```yaml
query:
  allOf: [FF-APP-001, FF-META-001]
  anyOf: [FF-RES-001, FF-RES-002]
```

`allOf` exige todas as funções em `PASS` ou `WAIVED`. `anyOf` exige ao menos uma.

## Política de ausência

`onUnknown` controla `UNKNOWN` e `STALE`. Use `BLOCK` somente quando a ausência da evidência for, por decisão de governança, uma violação equivalente ao descumprimento.
