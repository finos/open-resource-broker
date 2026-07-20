package org.finos.openresourcebroker.sdk.client;

import java.util.List;

/**
 * Filter/pagination parameters for
 * {@link OrbClient#listReturnRequests(ListReturnRequestsParams)} — the canonical
 * exposed set for {@code GET /api/v1/requests/return}, matching the
 * TypeScript/Go SDKs (limit/offset/cursor/q/sort/provider filters).
 *
 * <p>All fields are optional; unset (null) fields are omitted from the query.
 */
public class ListReturnRequestsParams {

    private Integer limit;
    private Integer offset;
    private String cursor;
    private String q;
    private String sort;
    private String providerName;
    private String providerType;
    private List<String> filterExpressions;

    public ListReturnRequestsParams limit(Integer v) { this.limit = v; return this; }
    public ListReturnRequestsParams offset(Integer v) { this.offset = v; return this; }
    public ListReturnRequestsParams cursor(String v) { this.cursor = v; return this; }
    public ListReturnRequestsParams q(String v) { this.q = v; return this; }
    public ListReturnRequestsParams sort(String v) { this.sort = v; return this; }
    public ListReturnRequestsParams providerName(String v) { this.providerName = v; return this; }
    public ListReturnRequestsParams providerType(String v) { this.providerType = v; return this; }
    public ListReturnRequestsParams filterExpressions(List<String> v) { this.filterExpressions = v; return this; }

    public Integer getLimit() { return limit; }
    public Integer getOffset() { return offset; }
    public String getCursor() { return cursor; }
    public String getQ() { return q; }
    public String getSort() { return sort; }
    public String getProviderName() { return providerName; }
    public String getProviderType() { return providerType; }
    public List<String> getFilterExpressions() { return filterExpressions; }
}
