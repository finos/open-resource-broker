package org.finos.openresourcebroker.sdk.client;

import java.util.Collections;
import java.util.List;

/**
 * A single status update from the ORB SSE stream.
 */
public class StreamEvent {

    private final String requestId;
    private final String status;
    private final String message;
    private final int requestedCount;
    private final int successfulCount;
    private final int failedCount;
    private final List<MachineInfo> machines;

    public StreamEvent(String requestId, String status, String message,
                       int requestedCount, int successfulCount, int failedCount,
                       List<MachineInfo> machines) {
        this.requestId = requestId;
        this.status = status;
        this.message = message;
        this.requestedCount = requestedCount;
        this.successfulCount = successfulCount;
        this.failedCount = failedCount;
        this.machines = machines != null ? machines : Collections.emptyList();
    }

    public String getRequestId() { return requestId; }
    public String getStatus() { return status; }
    public String getMessage() { return message; }
    public int getRequestedCount() { return requestedCount; }
    public int getSuccessfulCount() { return successfulCount; }
    public int getFailedCount() { return failedCount; }
    public List<MachineInfo> getMachines() { return machines; }

    /** Per-machine status info within a StreamEvent. */
    public static class MachineInfo {
        private final String machineId;
        private final String name;
        private final String status;
        private final String result;
        private final String privateIp;
        private final String publicIp;
        private final String launchTime;
        private final String message;

        public MachineInfo(String machineId, String name, String status, String result,
                           String privateIp, String publicIp, String launchTime, String message) {
            this.machineId = machineId;
            this.name = name;
            this.status = status;
            this.result = result;
            this.privateIp = privateIp;
            this.publicIp = publicIp;
            this.launchTime = launchTime;
            this.message = message;
        }

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
