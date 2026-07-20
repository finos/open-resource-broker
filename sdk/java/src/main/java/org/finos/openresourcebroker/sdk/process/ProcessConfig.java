package org.finos.openresourcebroker.sdk.process;

import java.time.Duration;
import java.util.List;
import java.util.Map;

/**
 * Configuration for the managed ORB subprocess.
 */
public class ProcessConfig {

    private String binary = "orb";
    private List<String> extraArgs;
    private Map<String, String> env;
    private String socketPath;
    private int port = 8000;
    private Duration startTimeout = Duration.ofSeconds(30);
    private Duration stopTimeout = Duration.ofSeconds(10);
    private String configPath; // path to orb config file

    public String getBinary() { return binary; }
    public void setBinary(String binary) { this.binary = binary; }

    public List<String> getExtraArgs() { return extraArgs; }
    public void setExtraArgs(List<String> extraArgs) { this.extraArgs = extraArgs; }

    public Map<String, String> getEnv() { return env; }
    public void setEnv(Map<String, String> env) { this.env = env; }

    public String getSocketPath() { return socketPath; }
    public void setSocketPath(String socketPath) { this.socketPath = socketPath; }

    public int getPort() { return port; }
    public void setPort(int port) { this.port = port; }

    public Duration getStartTimeout() { return startTimeout; }
    public void setStartTimeout(Duration startTimeout) { this.startTimeout = startTimeout; }

    public Duration getStopTimeout() { return stopTimeout; }
    public void setStopTimeout(Duration stopTimeout) { this.stopTimeout = stopTimeout; }

    public String getConfigPath() { return configPath; }
    public void setConfigPath(String configPath) { this.configPath = configPath; }

    // Builder pattern for ergonomic construction

    public static Builder builder() { return new Builder(); }

    public static class Builder {
        private final ProcessConfig cfg = new ProcessConfig();

        public Builder binary(String binary) { cfg.setBinary(binary); return this; }
        public Builder socketPath(String path) { cfg.setSocketPath(path); return this; }
        public Builder port(int port) { cfg.setPort(port); return this; }
        public Builder startTimeout(Duration d) { cfg.setStartTimeout(d); return this; }
        public Builder stopTimeout(Duration d) { cfg.setStopTimeout(d); return this; }
        public Builder extraArgs(List<String> args) { cfg.setExtraArgs(args); return this; }
        public Builder env(Map<String, String> env) { cfg.setEnv(env); return this; }
        public Builder configPath(String path) { cfg.setConfigPath(path); return this; }

        public ProcessConfig build() { return cfg; }
    }
}
