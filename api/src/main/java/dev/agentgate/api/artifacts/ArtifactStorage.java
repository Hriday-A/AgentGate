package dev.agentgate.api.artifacts;

import dev.agentgate.api.config.AppProperties;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.regex.Pattern;
import org.springframework.stereotype.Component;

/**
 * Screenshots on local disk: {storageDir}/{runId}/{taskId}/{filename}.
 * Every path segment is whitelisted and the resolved path must stay inside the
 * storage root, so a name like "../../etc/passwd" can never escape it.
 */
@Component
public class ArtifactStorage {

    private static final Pattern SEGMENT = Pattern.compile("[A-Za-z0-9._-]{1,100}");
    private final Path root;

    public ArtifactStorage(AppProperties props) {
        this.root = props.storageDir().toAbsolutePath().normalize();
    }

    public Path resolve(long runId, String taskId, String filename) {
        for (String segment : new String[] {taskId, filename}) {
            if (!SEGMENT.matcher(segment).matches() || segment.startsWith(".")) {
                throw new IllegalArgumentException("Invalid path segment");
            }
        }
        Path path = root.resolve(Long.toString(runId)).resolve(taskId).resolve(filename).normalize();
        if (!path.startsWith(root)) {
            throw new IllegalArgumentException("Path escapes storage root");
        }
        return path;
    }

    public void write(Path path, byte[] data) throws IOException {
        Files.createDirectories(path.getParent());
        Files.write(path, data);
    }
}
