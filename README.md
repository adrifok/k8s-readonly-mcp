k8s-readonly-mcp

A read-only Kubernetes MCP server, deployed and hardened on Kubernetes so AI agents
(Claude Code or any MCP client) can inspect a cluster without being able to change it.

The interesting part is not the server, which is about 100 lines of Python on the
official MCP SDK. It is how it runs: least-privilege RBAC, a restricted pod, token auth,
and secrets that live encrypted in Git.


What an agent can ask

  list_pods          phase, readiness, restarts and node for pods in a namespace
  list_deployments   ready/desired replicas and images
  rollout_status     whether a rollout finished, with its conditions
  list_events        recent events, optionally Warnings only

Responses are compact summaries, never full Kubernetes objects. Full pod specs carry env
values, which is where plaintext secrets usually leak, and they also waste LLM tokens.


Security model

  Read-only is enforced by RBAC, not by code. The ServiceAccount is bound to a
  ClusterRole with list on pods and events, and get/list on deployments. Nothing else:
  no secrets, no pod logs, no exec, no write verbs. If the code tried to delete
  something, the API server would answer 403.

  Verified with:

    kubectl auth can-i list pods -A --as=system:serviceaccount:mcp:k8s-readonly-mcp      yes
    kubectl auth can-i delete pods -A --as=system:serviceaccount:mcp:k8s-readonly-mcp    no
    kubectl auth can-i get secrets -A --as=system:serviceaccount:mcp:k8s-readonly-mcp    no
    kubectl auth can-i create pods --subresource=exec -A --as=system:serviceaccount:mcp:k8s-readonly-mcp    no

  The pod runs in a namespace labelled pod-security.kubernetes.io/enforce=restricted:
  non-root uid 10001, read-only root filesystem, no capabilities, no privilege
  escalation, seccomp RuntimeDefault. Admission rejects it if any of that is loosened.

  HTTP requests need a bearer token, compared in constant time. The server refuses to
  start in HTTP mode without one, so a missing secret never means an open endpoint.

  The token is stored in Git encrypted with SOPS and age. Only the values are
  encrypted, so metadata stays readable for reviews. Plaintext exists only in the pipe
  between sops and kubectl at deploy time.

  The Service is ClusterIP only, reached through kubectl port-forward. There is no
  Ingress or NodePort, because there is no TLS yet.


Layout

  src/k8s_readonly_mcp/   MCP server, stdio for local use, streamable HTTP in cluster
  Dockerfile              two-stage build with uv, runtime image has only the venv
  chart/                  Helm chart: ServiceAccount, RBAC, Deployment, Service
  k8s/secret.yaml         auth token Secret, SOPS-encrypted
  .sops.yaml              which files get encrypted and with which key


Run locally with Claude Code (stdio)

  Requires uv. Pin the kubeconfig so the server can never pick up another context:

    kubectl config view --minify --flatten --context minikube > ~/.kube/minikube.yaml && chmod 600 ~/.kube/minikube.yaml
    claude mcp add k8s-readonly -e KUBECONFIG=$HOME/.kube/minikube.yaml -- uv run --directory $PWD k8s-readonly-mcp


Deploy to minikube

  Requires minikube, helm, sops and an age key in ~/.config/sops/age/keys.txt. To use
  your own key, put its public key in .sops.yaml and regenerate k8s/secret.yaml (see
  Rotate the token).

    export KUBECONFIG=$HOME/.kube/minikube.yaml
    docker build -t k8s-readonly-mcp:0.1.0 .
    minikube image load k8s-readonly-mcp:0.1.0
    kubectl create namespace mcp && kubectl label namespace mcp pod-security.kubernetes.io/enforce=restricted
    sops decrypt k8s/secret.yaml | kubectl apply -f -
    helm upgrade --install k8s-readonly-mcp ./chart -n mcp
    kubectl -n mcp port-forward svc/k8s-readonly-mcp 8000:8000

  The MCP endpoint is then http://127.0.0.1:8000/mcp with header
  Authorization: Bearer <token>.


Rotate the token

    kubectl -n mcp create secret generic k8s-readonly-mcp-auth --from-literal=token="$(openssl rand -hex 32)" --dry-run=client -o yaml | sops encrypt --filename-override k8s/secret.yaml > k8s/secret.yaml
    sops decrypt k8s/secret.yaml | kubectl apply -f -
    kubectl -n mcp rollout restart deployment/k8s-readonly-mcp

  The pod reads the token at startup, hence the restart. Clients with the old token get
  401 from that moment.


Known limitations

  One static token shared by all clients, so audit logs cannot tell callers apart.
  The next step is OIDC with per-user tokens, which the SDK supports via token_verifier.

  No TLS in the server. Fine behind port-forward, not for anything exposed.

  The ClusterRole can read pods and events in every namespace, kube-system included.
  Swap it for per-namespace Roles if the scope must be narrower.

  Probes are TCP only, so they confirm the port is open, not that the Kubernetes API is
  reachable.

  Base images are not pinned by digest yet.


Roadmap

  EKS with Terraform: image in ECR, AWS KMS instead of age for SOPS, IRSA so nothing in
  the cluster holds static AWS keys.
  GitOps with ArgoCD and KSOPS, so decryption happens in the cluster instead of on a
  laptop.
  CI: image build, Trivy scan and digest pinning in GitHub Actions.
