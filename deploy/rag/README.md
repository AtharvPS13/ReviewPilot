# Repository-aware reviews

ReviewPilot can retrieve repository conventions before its existing LLM review stage.
The Action sends up to eight changed file paths with up to 3,000 characters of added
lines per file. The service searches only the deployment's configured repository and
each file's applicable path scopes, then returns the top two matches per file above
the similarity threshold. The LLM receives excerpts as untrusted JSON reference data.
ReviewPilot keeps convention findings only when their structured citation ID matches a
source returned for that same file and the comment starts and ends on added lines. It renders a
commit-pinned link with the Markdown line range or the JSON decision ID. Convention
findings are advisory text and carry no executable replacement code.

The feature is off unless the Action receives `rag-url`. Missing credentials, a timeout,
or a failed retrieval request emits a diagnostic and the usual review proceeds. The
service URL and credentials come from the workflow. The service is scoped to one
repository per deployment. On Render, retrieval requires a read-only bearer token
and indexing requires a separate writer token. Cloud Run uses IAM to control invocation
and also requires the writer token for indexing. Keep the writer token out of PR jobs.

## Corpus format

Index a trusted, committed manifest. Each item names a Markdown or JSON file and
may supply repository-relative directory or file scopes. Omit scopes for global guidance.
JSON files contain an array of decisions. Each record has `id`, `title`, `decision` and
optional `applies_to`. Record scopes override file scopes when provided. See
[the sample manifest](../../tests/fixtures/rag/manifest.json) and
[the sample decisions](../../tests/fixtures/rag/decisions.json).

The manifest is the entire desired snapshot. Remove an entry and re-index to delete
it, or submit an empty list to clear all guidance. Repeated indexing of the same commit
is idempotent and reuses embeddings. When a later commit changes source text, the
service updates the excerpts, embeds changed content, and deletes obsolete rows in one
transaction. Every citation uses the indexed commit, even when reviewing a newer PR.
Never index a PR head using the writer credential.

## Local checks

The offline fixture invokes the normal Action entry point, uses the real HTTP handler
via an in-process WSGI transport, and replaces PostgreSQL, embeddings, the review model,
and GitHub with deterministic local substitutes. Each fixture is run twice with the same
review-model settings.

```bash
python -m pip install -e '.[dev]'
python -m tests.rag_eval
python -m tests.rag_eval --loopback
python -m pytest tests/ -o addopts=
```

To exercise actual pgvector, use PostgreSQL 16 with the pgvector extension installed.
The following uses a disposable database with a fixture-only password.

```bash
docker run -d --rm --name reviewpilot-pg -p 127.0.0.1:5432:5432 \
  -e POSTGRES_DB=reviewpilot -e POSTGRES_USER=reviewpilot \
  -e POSTGRES_PASSWORD=fixture-only pgvector/pgvector:pg16
docker exec reviewpilot-pg pg_isready -U reviewpilot -d reviewpilot
docker exec reviewpilot-pg psql -U reviewpilot -d reviewpilot \
  -c 'CREATE EXTENSION IF NOT EXISTS vector'
python -m pip install -e '.[dev,rag-service]'
export REVIEWPILOT_TEST_PG_DSN=postgresql://reviewpilot:fixture-only@127.0.0.1:5432/reviewpilot
python -m pytest tests/test_rag.py -o addopts=
```

For an HTTP smoke test against that database, run the service in one terminal:

```bash
export RAG_DATABASE_URL=postgresql://reviewpilot:fixture-only@127.0.0.1:5432/reviewpilot
export RAG_REPO_ID=fixture/reviewpilot
export RAG_INDEX_TOKEN=fixture-writer
export RAG_EMBEDDER=fixture RAG_MIN_SIMILARITY=0.09
python -m reviewpilot.rag.service
```

Use a second terminal from the repository root after committing the fixture files:

```bash
export REVIEWPILOT_RAG_URL=http://127.0.0.1:8080
export RAG_REPO_ID=fixture/reviewpilot RAG_INDEX_TOKEN=fixture-writer
python -m reviewpilot.rag.index "$RAG_REPO_ID" --revision HEAD \
  --manifest tests/fixtures/rag/manifest.json
python -m tests.rag_eval --live-service
```

## Deploy on Render

1. Commit the code and fixtures to the default branch of a disposable GitHub repository.
   Make sure the existing Supabase database has the `vector` extension. Use the
   [Supabase Session pooler URI](https://supabase.com/docs/guides/database/connecting-to-postgres)
   with `sslmode=require`. Restrict database access to the service role or table owner.
   If Supabase's Data API exposes the table's schema, enable RLS on `reviewpilot_guidance`
   without public access policies or disable the Data API for that schema.
2. In Render, create a **Web Service** from that repository. Select **Docker**, use the
   repository root for the build context, enter `./Dockerfile.rag` as the Dockerfile path,
   and set `/healthz` as the health check path. Leave the Docker command and `PORT` at the
   defaults. The image starts Gunicorn on `0.0.0.0:$PORT`. The Free plan is suitable for
   a fixture demo. It can sleep when idle, so the evaluation warms the service first.
3. Set these values in the service's **Environment** settings. Keep database credentials
   and API keys out of Git and workflow variables.

   | Render variable | Value |
   | --- | --- |
   | `RAG_REPO_ID` | Lowercase `owner/repo` of the disposable repository |
   | `RAG_DATABASE_URL` | Supabase Session pooler URI with `sslmode=require` |
   | `RAG_READ_TOKEN` | Random retrieval token |
   | `RAG_INDEX_TOKEN` | Different random indexing token |
   | `RAG_EMBEDDER` | `openai` |
   | `OPENAI_API_KEY` | OpenAI API key with access to embeddings |

4. In the disposable repository, open **Settings → Secrets and variables → Actions**.
   Create these repository variables and secrets.

   | Type | GitHub name | Value |
   | --- | --- | --- |
   | Variable | `REVIEWPILOT_FIXTURE_REPO` | Same lowercase `owner/repo` as `RAG_REPO_ID` |
   | Variable | `REVIEWPILOT_RAG_URL` | Render service root, `https://<service>.onrender.com` |
   | Secret | `REVIEWPILOT_RAG_API_TOKEN` | Render `RAG_READ_TOKEN` value |
   | Secret | `RAG_INDEX_TOKEN` | Render `RAG_INDEX_TOKEN` value |
   | Secret | `OPENAI_API_KEY` | OpenAI API key with access to `gpt-4o-mini` |

5. Under **Actions**, run **ReviewPilot Render evaluation** on the default branch.
   It verifies health and a `401` response without a retrieval credential, indexes the
   committed fixture corpus, and runs the Action's Python entry point with real HTTPS
   and `gpt-4o-mini` at temperature 0. The workflow uses fixture PR inputs and captures
   comments in the job summary. It does not post to GitHub PRs.

Indexing and retrieval both use `text-embedding-3-small` at 768 dimensions. The
database tags vectors with the embedder identity, re-embeds a changed model's snapshot,
and searches only rows for the configured repository and embedder. Fixture indexing
replaces the snapshot only for the disposable repository ID. Review and embedding calls
can incur OpenAI API charges. The job summary reports eight reviews, with retrieval hits,
citation correctness, unexpected findings, and latency. Inspect any unexpected comments
and check both cited fixtures against the committed Markdown and JSON sources.

For a trusted PR job using the Docker Action, pass its `rag-url` input from the URL
variable and its `rag-api-token` input from the read-only secret. Do not pass the
indexing token into a PR job. The Render service root must use its `onrender.com` URL.

## Deploy on Google Cloud

Use a billing-enabled Google Cloud project. The commands below deploy the Python
service to Cloud Run and reuse Supabase for PostgreSQL. Enable the `vector` extension
in the Supabase database and use the [Session pooler connection string](https://supabase.com/docs/guides/database/connecting-to-postgres)
on port `5432` with `sslmode=require`. Use a database role that can create and use the
application table. Use a disposable GitHub repository for the fixture corpus so
indexing a complete fixture snapshot leaves real repository guidance untouched.

The following Bash commands assume a new service and that the operator has project IAM,
Cloud Build, Secret Manager and Cloud Run deployment access. Reuse existing accounts or
Artifact Registry repositories by skipping their create commands. Model inference
can incur charges.

```bash
export PROJECT=YOUR_BILLING_ENABLED_PROJECT
export REGION=asia-northeast3
export RAG_REPO_ID=your-github-owner/your-disposable-repo
export SERVICE=reviewpilot-guidance
export RUNTIME="reviewpilot-rag@${PROJECT}.iam.gserviceaccount.com"
export IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/reviewpilot/guidance:v1"
gcloud config set project "$PROJECT"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com aiplatform.googleapis.com \
  secretmanager.googleapis.com iamcredentials.googleapis.com
gcloud iam service-accounts create reviewpilot-rag
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:$RUNTIME" --role=roles/aiplatform.user
gcloud artifacts repositories create reviewpilot --location="$REGION" --repository-format=docker
```

Create the secrets interactively with shell tracing off. Enter the complete Supabase
Session pooler URI and URL-encode special characters in the password.
Use a password manager-generated random value for the indexing token and retain it in
the password manager for the indexing step.

```bash
read -rsp 'PostgreSQL connection string: ' db_secret; printf '\n'
printf '%s' "$db_secret" | gcloud secrets create reviewpilot-rag-db --data-file=-
unset db_secret
read -rsp 'Indexing token: ' RAG_INDEX_TOKEN; printf '\n'
printf '%s' "$RAG_INDEX_TOKEN" | gcloud secrets create reviewpilot-rag-index --data-file=-
for secret in reviewpilot-rag-db reviewpilot-rag-index; do
  gcloud secrets add-iam-policy-binding "$secret" \
    --member="serviceAccount:$RUNTIME" --role=roles/secretmanager.secretAccessor
done
unset RAG_INDEX_TOKEN
```

Confirm the project's Cloud Build service account can build and push to Artifact Registry.
The source upload uses the checked-in `.gcloudignore` allowlist. Cloud Run uses the runtime
service account for Vertex AI, Secret Manager injects the database URI and
writer token, and IAM requires an identity token for invocation.

```bash
gcloud builds submit --config deploy/rag/cloudbuild.yaml --substitutions="_IMAGE=$IMAGE" .
gcloud run deploy "$SERVICE" --image="$IMAGE" --region="$REGION" \
  --service-account="$RUNTIME" --no-allow-unauthenticated --execution-environment=gen2 \
  --timeout=600 --concurrency=4 --min-instances=0 --max-instances=1 \
  --set-env-vars="RAG_REPO_ID=$RAG_REPO_ID,GOOGLE_CLOUD_PROJECT=$PROJECT,VERTEX_LOCATION=global,RAG_EMBEDDER=vertex" \
  --set-secrets="RAG_DATABASE_URL=reviewpilot-rag-db:1,RAG_INDEX_TOKEN=reviewpilot-rag-index:1"
```

For Cloud SQL, enable `sqladmin.googleapis.com`, grant the runtime service account
`roles/cloudsql.client`, and use the [Cloud Run connection guide](https://docs.cloud.google.com/sql/docs/postgres/connect-run).
Add `--add-cloudsql-instances` with the instance connection name, set `PGHOST` to
`/cloudsql/INSTANCE_CONNECTION_NAME`, and store the database name, user, and password
as a libpq connection string in `reviewpilot-rag-db`.

### Verify private invocation and the Action

Grant your active Google account `roles/run.invoker` on the service for the manual smoke
test. The developer token is short-lived. The URL is the service root. Commit and
push the fixture corpus to the disposable repository so its citations resolve to the
indexed revision. Run the indexing and evaluation commands from that checkout.

```bash
CALLER=$(gcloud config get-value account)
gcloud run services add-iam-policy-binding "$SERVICE" --region="$REGION" \
  --member="user:$CALLER" --role=roles/run.invoker
export REVIEWPILOT_RAG_URL=$(gcloud run services describe "$SERVICE" --region="$REGION" \
  --format='value(status.url)')
curl -s -o /dev/null -w '%{http_code}\n' "$REVIEWPILOT_RAG_URL/healthz"
export REVIEWPILOT_RAG_TOKEN=$(gcloud auth print-identity-token)
curl -fsS -H "Authorization: Bearer $REVIEWPILOT_RAG_TOKEN" "$REVIEWPILOT_RAG_URL/healthz"
read -rsp 'Indexing token: ' RAG_INDEX_TOKEN; printf '\n'
export RAG_INDEX_TOKEN
python -m reviewpilot.rag.index "$RAG_REPO_ID" --revision HEAD \
  --manifest tests/fixtures/rag/manifest.json
unset RAG_INDEX_TOKEN
python -m tests.rag_eval --live-service
# Enter the OpenAI key privately for the paired real-model evaluation.
read -rsp 'OpenAI API key: ' OPENAI_API_KEY; printf '\n'
export OPENAI_API_KEY
python -m tests.rag_eval --live-service --real-model --details
unset OPENAI_API_KEY
unset REVIEWPILOT_RAG_TOKEN
```

Expect an unauthenticated rejection (401 or 403), an authenticated health response, and
a pinned API guideline citation in the Action fixture's captured comment. The harness
uses the Action's `main()` and real HTTPS retrieval in `--live-service` mode. GitHub
reads and writes are replaced with the fixture in both modes. `--real-model` also makes
live, billable review calls to `gpt-4o-mini` at temperature 0 for both sets of fixtures.
Both arms use the same four fixtures and model settings. The enabled arm embeds PR
queries with Vertex AI and retrieves guidance from Supabase. `--details` prints the
per-fixture results and captured comments for citation review. The false-positive
count flags comments outside the labeled expectation. Inspect those comments before
interpreting the count as a model error.

For a hosted PR workflow, set up [GitHub Workload Identity Federation](https://docs.cloud.google.com/iam/docs/workload-identity-federation-with-deployment-pipelines)
restricted to your repository and trusted workflow. Give its invoker service account
only `roles/run.invoker` on this service. Use `id-token: write` and obtain a Google
ID token whose audience is the service URL, per
[Cloud Run service authentication](https://docs.cloud.google.com/run/docs/authenticating/service-to-service).
Pass the auth step's output directly to the Action:

```yaml
- uses: google-github-actions/auth@v3
  id: cloud-auth
  with:
    workload_identity_provider: ${{ vars.RAG_WIF_PROVIDER }}
    service_account: ${{ vars.RAG_INVOKER_SERVICE_ACCOUNT }}
    create_credentials_file: false
    token_format: id_token
    id_token_audience: ${{ vars.REVIEWPILOT_RAG_URL }}
- uses: ./
  with:
    github-token: ${{ secrets.GITHUB_TOKEN }}
    llm-api-key: ${{ secrets.OPENAI_API_KEY }}
    rag-url: ${{ vars.REVIEWPILOT_RAG_URL }}
    rag-id-token: ${{ steps.cloud-auth.outputs.id_token }}
```

Keep this job on trusted events and branches that meet the federation policy. Leave
the existing review workflow without a RAG URL to retain its normal prompt and schema.
