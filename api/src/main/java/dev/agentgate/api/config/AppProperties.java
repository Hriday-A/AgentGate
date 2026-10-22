package dev.agentgate.api.config;

import java.nio.file.Path;
import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * @param appUrl     public URL of the dashboard; OAuth and logout redirect the browser here
 * @param storageDir where uploaded trace screenshots are stored
 * @param devLogin   enables POST /dev/login (local development only)
 */
@ConfigurationProperties("agentgate")
public record AppProperties(String appUrl, Path storageDir, boolean devLogin) {
}
