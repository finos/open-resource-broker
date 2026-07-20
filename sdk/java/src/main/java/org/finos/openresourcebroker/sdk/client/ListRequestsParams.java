package org.finos.openresourcebroker.sdk.client;

import java.util.List;

/**
 * Filter/pagination parameters for
 * {@link OrbClient#listRequests(ListRequestsParams)} — the canonical exposed set
 * for {@code GET /api/v1/requests/}, matching the TypeScript/Go SDKs.
 *
 * <p>All fields are optional; unset (null) fields are omitted from the query.
 * Use the fluent setters to build:
 * <pre>{@code
 * var params = new ListRequestsParams().status("pending").limit(50).sort("-created_at");
 * client.listRequests(params);
 * }</pre>
 */
public class ListRequestsParams {

    private String status;
    private Integer limit;
    private Integer offset;
    private Boolean sync;
    private String cursor;
    private String q;
    private String sort;
    private String providerName;
    private String providerType;
    private String templateId;
    private String requestType;
    private List<String> filterExpressions;

    public ListRequestsParams status(String v) { this.status = v; return this; }
    public ListRequestsParams limit(Integer v) { this.limit = v; return this; }
    public ListRequestsParams offset(Integer v) { this.offset = v; return this; }
    public ListRequestsParams sync(Boolean v) { this.sync = v; return this; }
    public ListRequestsParams cursor(String v) { this.cursor = v; return this; }
    public ListRequestsParams q(String v) { this.q = v; return this; }
    public ListRequestsParams sort(String v) { this.sort = v; return this; }
    public ListRequestsParams providerName(String v) { this.providerName = v; return this; }
    public ListRequestsParams providerType(String v) { this.providerType = v; return this; }
    public ListRequestsParams templateId(String v) { this.templateId = v; return this; }
    public ListRequestsParams requestType(String v) { this.requestType = v; return this; }
    public ListRequestsParams filterExpressions(List<String> v) { this.filterExpressions = v; return this; }

    public String getStatus() { return status; }
    public Integer getLimit() { return limit; }
    public Integer getOffset() { return offset; }
    public Boolean getSync() { return sync; }
    public String getCursor() { return cursor; }
    public String getQ() { return q; }
    public String getSort() { return sort; }
    public String getProviderName() { return providerName; }
    public String getProviderType() { return providerType; }
    public String getTemplateId() { return templateId; }
    public String getRequestType() { return requestType; }
    public List<String> getFilterExpressions() { return filterExpressions; }
}
