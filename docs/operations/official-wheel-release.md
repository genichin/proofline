# 공식 Wheel Release 운영 계약

## 목적

ProofLine official GitHub Release의 wheel을 exact source commit·version·checksum에 결합하고, local candidate와 remote download를 각각 isolated installed artifact로 검증한다.

```text
prepare → publication approval → publish → remote read-back verification
```

Package publication은 적용 project의 deployment나 `.proofline/` schema migration이 아니다.

## Release Identity

`v0.1.0` release의 identity는 다음과 같다.

```text
Distribution: proofline
Version:      0.1.0
Git tag:      v0.1.0
Wheel:        proofline-0.1.0-py3-none-any.whl
Checksum:     SHA256SUMS
Repository:   https://github.com/genichin/proofline
```

Tag는 annotated tag이며 exact release candidate commit 하나만 가리킨다. Release asset allowlist는 wheel과 `SHA256SUMS` 두 파일뿐이다. Sdist, source archive, installer와 update artifact는 이 계약의 package asset이 아니다. GitHub가 tag에 대해 자동 제공하는 source archive는 release upload asset으로 취급하지 않는다.

## Prepare

Publication 전에 다음을 모두 확인한다.

1. Candidate branch와 working tree가 clean하다.
2. Candidate commit에서 `proofline validate`와 전체 source test가 통과한다.
3. `pyproject.toml`의 name·version·Python requirement가 release identity와 일치한다.
4. Local·remote main, existing tag, Release와 asset 충돌을 read-only로 검사한다.
5. Unique temporary dist directory에서 다음 명령으로 wheel만 build한다.

```bash
uv build --wheel --out-dir "$DIST_DIR"
```

6. Exact wheel bytes의 checksum file을 생성한다.

```bash
(
  cd "$DIST_DIR"
  sha256sum proofline-0.1.0-py3-none-any.whl > SHA256SUMS
  sha256sum --check --strict SHA256SUMS
)
```

7. Fresh environment를 source checkout 밖에 만들고 wheel을 non-editable로 설치한다.

```bash
uv venv "$WHEEL_ENV"
uv pip install --python "$WHEEL_ENV/bin/python" \
  "$DIST_DIR/proofline-0.1.0-py3-none-any.whl"
```

8. Installed module이 해당 environment의 `site-packages`에서 import되고 distribution metadata version, `proofline --version`, packaged template byte parity와 representative `proofline validate` 성공·실패가 모두 일치하는지 확인한다.
9. 설치 전후 applied-project snapshot을 비교해 `pyproject.toml`, lockfile, `.venv`, `.proofline/` bytes와 Git status가 변하지 않았는지 확인한다.

Candidate source나 package resource가 바뀌면 이전 wheel evidence는 stale이다. 새 temporary directory에서 다시 build하고 전체 installed-artifact 검증을 반복한다.

## Publication Approval

Prepare evidence에는 다음을 포함한다.

- Exact candidate commit
- Wheel filename과 SHA-256
- Source·wheel verification 결과
- Planned tag, Release title와 asset allowlist
- Existing remote object preflight 결과

사용자가 이 exact candidate publication을 승인하기 전에는 tag create·push, GitHub Release 생성 또는 asset upload를 수행하지 않는다.

## Publish

승인 후에도 remote preflight를 다시 수행한다.

- `origin`이 expected public repository인지 확인한다.
- Remote main ancestry와 exact candidate reachability를 확인한다.
- `v0.1.0` tag가 없거나 exact expected commit을 가리키는지 확인한다.
- Existing Release와 assets가 없거나 exact plan과 일치하는지 확인한다.
- GitHub authentication은 상태와 required permission만 확인하고 credential 값을 출력·보존하지 않는다.

새 publication은 다음 순서로 수행한다.

1. Exact candidate에 annotated `v0.1.0` tag 생성
2. Tag object와 peeled commit 검증
3. Tag push
4. Existing tag를 exact identity로 read-back 검증
5. GitHub Release `v0.1.0` 생성
6. Wheel과 `SHA256SUMS` upload
7. Release metadata와 exact asset allowlist read-back 검증

Existing object가 exact plan과 일치하면 verified step으로 재사용할 수 있다. Commit, tag type, release metadata, filename 또는 checksum이 다르면 중단한다. Force tag, asset overwrite와 remote object 자동 삭제는 금지한다.

## Remote Read-Back Verification

Publication 후 fresh directory에 GitHub Release assets를 내려받는다.

```bash
gh release download v0.1.0 \
  --repo genichin/proofline \
  --dir "$REMOTE_DIST" \
  --pattern 'proofline-0.1.0-py3-none-any.whl' \
  --pattern 'SHA256SUMS'
```

다음을 다시 검증한다.

1. Asset filename 집합이 allowlist와 일치한다.
2. `sha256sum --check --strict SHA256SUMS`가 통과한다.
3. Downloaded wheel SHA-256이 local candidate digest와 일치한다.
4. Wheel metadata name·version·requires-python과 tag identity가 일치한다.
5. Fresh non-editable installation의 provenance, `proofline --version`, resource byte parity와 representative CLI가 통과한다.
6. Remote tag의 peeled commit이 approved candidate commit과 일치한다.

DQC evidence는 tag, Release URL, candidate commit, remote asset digest와 verification 결과를 기록한다.

## Partial Failure와 Retry

Remote publication은 local rollback으로 원상복구할 수 없다.

- Tag push 후 Release 생성 실패: tag를 삭제하지 않고 exact tag를 검증한 뒤 Release 생성부터 재개한다.
- Release 생성 후 asset 일부 upload 실패: existing asset의 bytes·digest를 검증하고 누락 asset만 upload한다.
- Existing asset digest conflict: overwrite하지 않고 실패한다.
- Remote download 또는 installation 실패: Release를 성공으로 판정하지 않고 원인을 해결한 새 evidence를 요구한다.

재시도는 이미 성공한 exact step을 재사용하고 누락 단계만 수행한다. Mutable `main`, branch name 또는 local filename만으로 기존 object를 신뢰하지 않는다.

## 제외 범위

- PyPI publication
- `curl ... | bash` installer
- rollback command
- Hosted CI와 Python matrix
- License 선택, signing, SBOM
- Application dependency 또는 canonical artifact migration

## v0.2.0 Update Release

`v0.2.0`은 동일한 prepare·approval·publish·remote read-back 절차를 사용한다.

```text
Tag:      v0.2.0
Wheel:    proofline-0.2.0-py3-none-any.whl
Checksum: SHA256SUMS
```

Fresh installed wheel에서 `proofline update --help`와 `proofline update --check`를 추가 검증한다. Source installation은 `--adopt-official` 없이는 전환되지 않아야 하며 `v0.1.0` 사용자의 one-time verified wheel install 절차를 tool-environment 문서와 skill에 기록한다.
