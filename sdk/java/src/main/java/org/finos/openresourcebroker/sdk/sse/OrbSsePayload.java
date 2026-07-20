package org.finos.openresourcebroker.sdk.sse;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;

import java.util.Collections;
import java.util.List;

/**
 * JSON structure of an ORB SSE data frame.
 *
 * <pre>
 * {
 *   "requests": [
 *     {
 *       "request_id": "...",
 *       "status": "...",
 *       "message": "...",
 *       "requested_count": 1,
 *       "successful_count": 0,
 *       "failed_count": 0,
 *       "machines": [...]
 *     }
 *   ]
 * }
 * </pre>
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public class OrbSsePayload {

    @JsonProperty("requests")
    private List<OrbSseRequest> requests = Collections.emptyList();

    public List<OrbSseRequest> getRequests() {
        return requests;
    }

    public OrbSseRequest firstRequest() {
        return requests != null && !requests.isEmpty() ? requests.get(0) : null;
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class OrbSseRequest {
        @JsonProperty("request_id") private String requestId;
        @JsonProperty("status") private String status;
        @JsonProperty("message") private String message;
        @JsonProperty("requested_count") private int requestedCount;
        @JsonProperty("successful_count") private int successfulCount;
        @JsonProperty("failed_count") private int failedCount;
        @JsonProperty("machines") private List<OrbSseMachine> machines = Collections.emptyList();

        public String getRequestId() { return requestId; }
        public String getStatus() { return status; }
        public String getMessage() { return message; }
        public int getRequestedCount() { return requestedCount; }
        public int getSuccessfulCount() { return successfulCount; }
        public int getFailedCount() { return failedCount; }
        public List<OrbSseMachine> getMachines() { return machines; }
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public static class OrbSseMachine {
        @JsonProperty("machine_id") private String machineId;
        @JsonProperty("name") private String name;
        @JsonProperty("status") private String status;
        @JsonProperty("result") private String result;
        @JsonProperty("private_ip") private String privateIp;
        @JsonProperty("public_ip") private String publicIp;
        @JsonProperty("launch_time") private String launchTime;
        @JsonProperty("message") private String message;

        public String getMachineId() { return machineId; }
        public String getName() { return name; }
        public String getStatus() { return status; }
        public String getResult() { return result; }
        public String getPrivateIp() { return privateIp; }
        public String getPublicIp() { return publicIp; }
        public String getLaunchTime() { return launchTime; }
        public String getMessage() { return message; }
    }
}
