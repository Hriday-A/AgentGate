package dev.agentgate.api.artifacts;

import dev.agentgate.api.auth.CurrentUser;
import dev.agentgate.api.auth.Errors;
import dev.agentgate.api.domain.Run;
import dev.agentgate.api.domain.RunRepository;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Map;
import org.springframework.http.CacheControl;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class ArtifactController {

    private static final Map<String, MediaType> TYPES = Map.of(
            "jpg", MediaType.IMAGE_JPEG, "jpeg", MediaType.IMAGE_JPEG, "png", MediaType.IMAGE_PNG);
    private static final int MAX_BYTES = 5 * 1024 * 1024;

    private final CurrentUser currentUser;
    private final RunRepository runs;
    private final ArtifactStorage storage;

    public ArtifactController(CurrentUser currentUser, RunRepository runs, ArtifactStorage storage) {
        this.currentUser = currentUser;
        this.runs = runs;
        this.storage = storage;
    }

    /** Runner upload (X-Api-Key): the run is looked up by the runner's own run id, scoped to the key's agent. */
    @PutMapping("/api/artifacts/runs/{externalId}/{taskId}/{filename}")
    public ResponseEntity<Void> upload(@PathVariable String externalId, @PathVariable String taskId,
                                       @PathVariable String filename, @RequestBody byte[] body,
                                       @RequestHeader(value = "Content-Type", required = false) String contentType)
            throws IOException {
        Run run = runs.findByAgentAndExternalId(currentUser.requireAgent(), externalId)
                .orElseThrow(() -> new Errors.NotFound("Ingest the run before uploading its artifacts."));
        mediaType(filename);
        if (body.length == 0 || body.length > MAX_BYTES) {
            return ResponseEntity.status(HttpStatus.PAYLOAD_TOO_LARGE).build();
        }
        storage.write(storage.resolve(run.getId(), taskId, filename), body);
        return ResponseEntity.status(HttpStatus.CREATED).build();
    }

    /** Dashboard download (session): only the owner of the run's agent can read it. */
    @GetMapping("/api/artifacts/{runId}/{taskId}/{filename}")
    public ResponseEntity<byte[]> download(@PathVariable long runId, @PathVariable String taskId,
                                           @PathVariable String filename) throws IOException {
        Run run = runs.findByIdAndAgent_Owner(runId, currentUser.require())
                .orElseThrow(() -> new Errors.NotFound("Not found."));
        Path path = storage.resolve(run.getId(), taskId, filename);
        if (!Files.isRegularFile(path)) {
            throw new Errors.NotFound("Not found.");
        }
        return ResponseEntity.ok()
                .contentType(mediaType(filename))
                .cacheControl(CacheControl.maxAge(java.time.Duration.ofDays(7)).cachePrivate())
                .body(Files.readAllBytes(path));
    }

    private static MediaType mediaType(String filename) {
        String ext = filename.substring(filename.lastIndexOf('.') + 1).toLowerCase();
        MediaType type = TYPES.get(ext);
        if (type == null) {
            throw new IllegalArgumentException("Only .jpg and .png screenshots are accepted");
        }
        return type;
    }

    @ExceptionHandler(Errors.Unauthorized.class)
    ResponseEntity<String> unauthorized(Errors.Unauthorized e) {
        return ResponseEntity.status(HttpStatus.UNAUTHORIZED).body(e.getMessage());
    }

    @ExceptionHandler(Errors.NotFound.class)
    ResponseEntity<String> notFound(Errors.NotFound e) {
        return ResponseEntity.status(HttpStatus.NOT_FOUND).body(e.getMessage());
    }

    @ExceptionHandler(IllegalArgumentException.class)
    ResponseEntity<String> badRequest(IllegalArgumentException e) {
        return ResponseEntity.badRequest().body(e.getMessage());
    }
}
