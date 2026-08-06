# Legacy non-terminal history migration 운영 절차

이 절차는 history policy 도입 전에 complete implementation·IQC cycle을 끝냈지만 아직 `in_progress` 또는 `verifying`인 fieldless Line 하나를 수동으로 adoption할 때만 사용한다. Writer나 bulk migration command는 없다.

## 1. Eligibility 확인

Repository first-parent 전체가 존재하는 clean checkout에서 `proofline validate`의 기존 진단을 확인한다. 대상 Line은 최초 policy activation `A0`의 strict ancestor에서 생성됐고, `A0` 전에 approved Micro-SPEC, 별도 persisted start `P`, non-governance implementation `I`, exact-bound IQC `Q`의 complete valid cycle을 가져야 한다. `not_started`, terminal, evidence-absent, post-activation, 이미 policy-bearing이거나 malformed history인 Line은 migration하지 않는다.

## 2. B^1 inventory 고정

현재 exact parent를 `B^1`로 고정한다. `B^1` tree의 대상 Line 아래 canonical Micro-SPEC, IQC와 존재하는 DQC path를 전수 조사한다. 각 entry가 `100644` 또는 `100755` regular blob인지 확인하고 repository object format의 lowercase blob OID를 기록한다. Path는 중복 없이 lexicographic order로 정렬한다.

## 3. 두 path만 작성

`.proofline/lines/line-NNNN/legacy-migration-NNNN.md`를 frontmatter-only로 작성한다. `id`, `line`, `pre_migration_parent: B^1`, nonempty `evidence`만 사용한다. 같은 change에서 `line-NNNN.md`에 `implementation_history: first_parent` 한 field만 추가한다. 다른 frontmatter/body, 제품 또는 canonical path를 변경하지 않는다.

두 path만 명시적으로 stage하고 commit한다. 이 containing commit이 migration baseline `B`이며 changed-path 집합은 Line과 migration artifact 두 개와 정확히 같아야 한다.

## 4. 검증과 오류 처리

Commit 전 오류는 아직 persisted되지 않은 두 path를 교정한 뒤 다시 검증한다. Commit 뒤 `proofline validate`를 실행해 path-bound `migration.schema.*`, `migration.path.*`, `migration.parent.*`, `migration.baseline.*`, `migration.eligibility.*`, `migration.inventory.*` 또는 `history.*` 진단을 그대로 처리한다.

Persisted migration artifact나 target policy를 수정·삭제·재도입하거나 두 번째 migration을 적용하지 않는다. 잘못된 `B`를 historical rewrite, terminal 위장 또는 stale OID 치환으로 고치지 않는다.

## 5. Fresh recovery

Migration은 기존 state/result를 승격하지 않고 pre-`B` cycle의 `I < B`만 예외로 인정한다. Delivery를 계속하려면 별도 persisted rework start부터 fresh `B < P₂ < I₂ < Q₂ < V₂ < DQC` chronology를 만든다. Pre-`B` implementation, IQC, integration 또는 DQC evidence를 fresh recovery evidence로 재사용하지 않는다.
