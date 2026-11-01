{{/*
Expand the name of the chart.
*/}}
{{- define "orb.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully-qualified app name.
Truncate to 63 chars because Kubernetes DNS naming constraints.
*/}}
{{- define "orb.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart label value (name-version).
*/}}
{{- define "orb.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels applied to all resources.
*/}}
{{- define "orb.labels" -}}
helm.sh/chart: {{ include "orb.chart" . }}
{{ include "orb.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels used by the Deployment and Service.
*/}}
{{- define "orb.selectorLabels" -}}
app.kubernetes.io/name: {{ include "orb.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Resolve the ServiceAccount name.
*/}}
{{- define "orb.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "orb.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Fail the render for unsupported storage and replica combinations.
*/}}
{{- define "orb.validate" -}}
{{- if not (has .Values.storage.type (list "json" "sqlite")) }}
{{- fail (printf "storage.type must be json or sqlite, got %q" (toString .Values.storage.type)) }}
{{- end }}
{{- if gt (int .Values.replicaCount) 1 }}
{{- fail (printf "replicaCount=%d is not supported with storage.type=%s: json and sqlite storage allow a single writer. Use replicaCount: 1." (int .Values.replicaCount) .Values.storage.type) }}
{{- end }}
{{- if and .Values.auth.enabled (eq .Values.auth.strategy "none") }}
{{- fail "auth.enabled is true but auth.strategy is none: set auth.strategy to bearer_token or disable auth" }}
{{- end }}
{{- if and (not .Values.auth.enabled) (ne .Values.auth.strategy "none") }}
{{- fail (printf "auth.strategy=%s has no effect while auth.enabled is false" .Values.auth.strategy) }}
{{- end }}
{{- if and (eq .Values.auth.strategy "bearer_token") (not .Values.auth.existingSecret) }}
{{- fail "auth.strategy=bearer_token requires auth.existingSecret: the name of a Secret holding the JWT signing key (at least 32 bytes)" }}
{{- end }}
{{- if not (has .Values.ui.mode (list "embedded" "split" "disabled")) }}
{{- fail (printf "ui.mode must be embedded, split or disabled, got %q" (toString .Values.ui.mode)) }}
{{- end }}
{{- if not .Values.image.tag }}
{{- fail "image.tag is required: set it to the ORB image version to deploy. Images up to and including 1.9.2 lack fixes this chart relies on (public probe paths under /orb with auth enabled, UI state on a read-only root filesystem), so the chart does not default to its appVersion." }}
{{- end }}
{{- if and (eq .Values.provider.type "k8s") .Values.rbac.create (not .Values.rbac.clusterScoped) }}
{{- if has "*" .Values.provider.k8s.namespaces }}
{{- fail "provider.k8s.namespaces [\"*\"] watches every namespace and needs rbac.clusterScoped: true" }}
{{- end }}
{{- if .Values.provider.k8s.nodeWatchEnabled }}
{{- fail "provider.k8s.nodeWatchEnabled needs rbac.clusterScoped: true: nodes are cluster-scoped" }}
{{- end }}
{{- end }}
{{- if and (has "*" .Values.provider.k8s.namespaces) (gt (len .Values.provider.k8s.namespaces) 1) }}
{{- fail "provider.k8s.namespaces: \"*\" cannot be combined with other namespaces" }}
{{- end }}
{{- end }}

{{/*
Namespaces the k8s provider manages: provider.k8s.namespaces, or the release
namespace when that list is empty. Returned as a JSON list.
*/}}
{{- define "orb.k8sNamespaces" -}}
{{- if .Values.provider.k8s.namespaces }}
{{- toJson .Values.provider.k8s.namespaces }}
{{- else }}
{{- toJson (list .Release.Namespace) }}
{{- end }}
{{- end }}

{{/*
Namespace the k8s provider uses when a template names none: the release
namespace when it is managed (or the watch is cluster-scoped), otherwise the
first managed namespace.
*/}}
{{- define "orb.k8sDefaultNamespace" -}}
{{- $namespaces := include "orb.k8sNamespaces" . | fromJsonArray }}
{{- if or (has .Release.Namespace $namespaces) (has "*" $namespaces) }}
{{- .Release.Namespace }}
{{- else }}
{{- first $namespaces }}
{{- end }}
{{- end }}

{{/*
URL prefix under which the API is served: /orb in embedded UI mode, none otherwise.
*/}}
{{- define "orb.apiPrefix" -}}
{{- if eq .Values.ui.mode "embedded" }}/orb{{ end }}
{{- end }}

{{/*
Resolve the HTTP path of one probe. Arguments: dict "root" <context> "probe" <probe values> "default" <path>.
Precedence: per-probe path, probes.pathOverride, computed default.
*/}}
{{- define "orb.probePath" -}}
{{- if .probe.path }}{{ .probe.path }}
{{- else if .root.Values.probes.pathOverride }}{{ .root.Values.probes.pathOverride }}
{{- else }}{{ include "orb.apiPrefix" .root }}{{ .default }}
{{- end }}
{{- end }}

{{/*
Name of the PersistentVolumeClaim holding /app/data.
*/}}
{{- define "orb.claimName" -}}
{{- default (printf "%s-data" (include "orb.fullname" .)) .Values.storage.persistence.existingClaim }}
{{- end }}

{{/*
Resolve the container image (repo:tag).
*/}}
{{- define "orb.image" -}}
{{- printf "%s:%s" .Values.image.repository (toString .Values.image.tag) }}
{{- end }}
