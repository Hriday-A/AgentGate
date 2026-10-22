package dev.agentgate.api;

import static org.assertj.core.api.Assertions.assertThat;

import java.io.IOException;
import java.net.CookieManager;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.web.server.LocalServerPort;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.test.context.ActiveProfiles;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

/**
 * End-to-end over real HTTP against a real PostgreSQL database: sign-in, agent
 * registration, API-key ingest, ownership rules and screenshot storage.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@ActiveProfiles("test")
class ApiIntegrationTest {

    @LocalServerPort
    int port;

    @Autowired
    JdbcTemplate jdbc;

    final ObjectMapper json = new ObjectMapper();

    @BeforeEach
    void cleanDatabase() {
        jdbc.execute("truncate users, agents, runs, task_results, steps restart identity cascade");
    }

    // --- helpers ---------------------------------------------------------------

    /** A browser-like client with its own cookie jar (= its own session). */
    HttpClient browser() {
        return HttpClient.newBuilder().cookieHandler(new CookieManager()).build();
    }

    HttpClient signedIn(String login) throws Exception {
        HttpClient client = browser();
        var resp = client.send(HttpRequest.newBuilder(uri("/dev/login?login=" + login))
                .POST(HttpRequest.BodyPublishers.noBody()).build(), HttpResponse.BodyHandlers.ofString());
        assertThat(resp.statusCode()).isEqualTo(200);
        return client;
    }

    URI uri(String path) {
        return URI.create("http://localhost:" + port + path);
    }

    JsonNode gql(HttpClient client, String query, Map<String, Object> variables, String apiKey) throws Exception {
        var builder = HttpRequest.newBuilder(uri("/graphql"))
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString(
                        json.writeValueAsString(Map.of("query", query, "variables", variables))));
        if (apiKey != null) {
            builder.header("X-Api-Key", apiKey);
        }
        var resp = client.send(builder.build(), HttpResponse.BodyHandlers.ofString());
        return json.readTree(resp.body());
    }

    JsonNode gql(HttpClient client, String query) throws Exception {
        return gql(client, query, Map.of(), null);
    }

    String errorType(JsonNode resp) {
        return resp.path("errors").path(0).path("extensions").path("classification").asString();
    }

    String createAgent(HttpClient owner, String name) throws Exception {
        JsonNode resp = gql(owner, "mutation($n: String!) { createAgent(name: $n) { apiKey agent { id } } }",
                Map.of("n", name), null);
        return resp.path("data").path("createAgent").path("apiKey").asString();
    }

    static Map<String, Object> runInput(String externalId) {
        return Map.of("input", Map.of(
                "externalId", externalId, "label", "graph/test-model", "agentKind", "graph", "model", "test-model",
                "summary", Map.of("tasks", 2, "passed", 1, "successRate", 0.5, "fieldAccuracy", 0.75,
                        "avgSteps", 6.5, "p50Seconds", 20.0, "p95Seconds", 30.0, "inputTokens", 1000,
                        "outputTokens", 100, "totalCostUsd", 0.0),
                "results", List.of(
                        task("easy-acme-amount", "lookup_amount", "easy", true, null, null, 5,
                                List.of(Map.of("stepNo", 1, "action", "click", "args", "{}", "ok", true,
                                        "verified", true, "screenshot", "step_001.jpg"))),
                        task("hard-acme-enter", "enter_invoice", "hard", false, "max_steps", 0.5, 8, null))));
    }

    /** One TaskResultInput; a HashMap because Map.of can't hold nulls or more than 10 entries. */
    static Map<String, Object> task(String id, String kind, String difficulty, boolean passed, String failure,
                                    Double fieldAccuracy, int steps, List<Map<String, Object>> trace) {
        Map<String, Object> t = new HashMap<>();
        t.put("taskId", id);
        t.put("kind", kind);
        t.put("vendor", "acme");
        t.put("difficulty", difficulty);
        t.put("passed", passed);
        t.put("failure", failure);
        t.put("fieldAccuracy", fieldAccuracy);
        t.put("steps", steps);
        t.put("seconds", passed ? 20.0 : 30.0);
        t.put("inputTokens", 500);
        t.put("outputTokens", 50);
        t.put("trace", trace);
        return t;
    }

    static final String INGEST = "mutation($input: RunInput!) { ingestRun(input: $input) { id } }";

    // --- tests -------------------------------------------------------------------

    @Test
    void anonymousCallersGetNoData() throws Exception {
        assertThat(gql(browser(), "{ me { login } }").path("data").path("me").isNull()).isTrue();
        assertThat(errorType(gql(browser(), "{ agents { id } }"))).isEqualTo("UNAUTHORIZED");
    }

    @Test
    void createAgentReturnsKeyOnceAndStoresOnlyItsHash() throws Exception {
        HttpClient alice = signedIn("alice");
        String key = createAgent(alice, "invoice-bot");
        assertThat(key).startsWith("ag_").hasSize(46);

        JsonNode agents = gql(alice, "{ agents { name apiKeyPrefix } }").path("data").path("agents");
        assertThat(agents).hasSize(1);
        assertThat(agents.path(0).path("apiKeyPrefix").asString()).isEqualTo(key.substring(0, 10));
        String stored = jdbc.queryForObject("select api_key_hash from agents", String.class);
        assertThat(stored).hasSize(64).doesNotContain(key);

        assertThat(errorType(gql(alice, "mutation { createAgent(name: \"invoice-bot\") { apiKey } }")))
                .isEqualTo("BAD_REQUEST");
    }

    @Test
    void runnerIngestsWithApiKeyAndOwnerSeesTheRun() throws Exception {
        HttpClient alice = signedIn("alice");
        String key = createAgent(alice, "invoice-bot");

        String runId = gql(browser(), INGEST, runInput("20261022-120000"), key)
                .path("data").path("ingestRun").path("id").asString();
        assertThat(runId).isNotBlank();

        JsonNode run = gql(alice, """
                query($id: ID!) { run(id: $id) {
                  successRate fieldAccuracy byDifficulty { difficulty tasks passed successRate }
                  results(onlyFailed: true) { taskId failure }
                  all: results { taskId trace { stepNo action screenshotUrl } } } }""",
                Map.of("id", runId), null).path("data").path("run");
        assertThat(run.path("successRate").asDouble()).isEqualTo(0.5);
        assertThat(run.path("byDifficulty").path(0).path("difficulty").asString()).isEqualTo("easy");
        assertThat(run.path("byDifficulty").path(1).path("successRate").asDouble()).isEqualTo(0.0);
        assertThat(run.path("results")).hasSize(1);
        assertThat(run.path("results").path(0).path("failure").asString()).isEqualTo("max_steps");
        assertThat(run.path("all").path(0).path("trace").path(0).path("screenshotUrl").asString())
                .isEqualTo("/api/artifacts/" + runId + "/easy-acme-amount/step_001.jpg");
    }

    @Test
    void reIngestingTheSameRunUpdatesItInPlace() throws Exception {
        HttpClient alice = signedIn("alice");
        String key = createAgent(alice, "invoice-bot");
        String first = gql(browser(), INGEST, runInput("run-1"), key).path("data").path("ingestRun").path("id").asString();
        String second = gql(browser(), INGEST, runInput("run-1"), key).path("data").path("ingestRun").path("id").asString();
        assertThat(second).isEqualTo(first);  // same id, so links and screenshot paths stay valid
        assertThat(jdbc.queryForObject("select count(*) from runs", Integer.class)).isEqualTo(1);
        assertThat(jdbc.queryForObject("select count(*) from task_results", Integer.class)).isEqualTo(2);
    }

    @Test
    void otherUsersCannotSeeYourRuns() throws Exception {
        HttpClient alice = signedIn("alice");
        String key = createAgent(alice, "invoice-bot");
        String runId = gql(browser(), INGEST, runInput("run-1"), key).path("data").path("ingestRun").path("id").asString();

        HttpClient bob = signedIn("bob");
        assertThat(gql(bob, "{ agents { id } }").path("data").path("agents")).isEmpty();
        assertThat(errorType(gql(bob, "query($id: ID!) { run(id: $id) { id } }", Map.of("id", runId), null)))
                .isEqualTo("NOT_FOUND");
    }

    @Test
    void ingestNeedsAValidApiKey() throws Exception {
        assertThat(errorType(gql(browser(), INGEST, runInput("run-1"), null))).isEqualTo("UNAUTHORIZED");
        var resp = browser().send(HttpRequest.newBuilder(uri("/graphql")).header("X-Api-Key", "ag_wrong")
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString("{\"query\":\"{ me { id } }\"}")).build(),
                HttpResponse.BodyHandlers.ofString());
        assertThat(resp.statusCode()).isEqualTo(401);
    }

    @Test
    void rotatingTheKeyRevokesTheOldOne() throws Exception {
        HttpClient alice = signedIn("alice");
        String oldKey = createAgent(alice, "invoice-bot");
        String agentId = gql(alice, "{ agents { id } }").path("data").path("agents").path(0).path("id").asString();
        String newKey = gql(alice, "mutation($id: ID!) { rotateApiKey(agentId: $id) { apiKey } }",
                Map.of("id", agentId), null).path("data").path("rotateApiKey").path("apiKey").asString();
        assertThat(newKey).isNotEqualTo(oldKey);
        assertThat(gql(browser(), INGEST, runInput("run-2"), newKey).path("data").path("ingestRun").isObject()).isTrue();
        var resp = browser().send(HttpRequest.newBuilder(uri("/graphql")).header("X-Api-Key", oldKey)
                .header("Content-Type", "application/json")
                .POST(HttpRequest.BodyPublishers.ofString("{\"query\":\"{ me { id } }\"}")).build(),
                HttpResponse.BodyHandlers.ofString());
        assertThat(resp.statusCode()).isEqualTo(401);
    }

    @Test
    void screenshotsUploadWithKeyAndDownloadForOwnerOnly() throws Exception {
        HttpClient alice = signedIn("alice");
        String key = createAgent(alice, "invoice-bot");
        String runId = gql(browser(), INGEST, runInput("run-1"), key).path("data").path("ingestRun").path("id").asString();
        byte[] jpeg = {(byte) 0xff, (byte) 0xd8, (byte) 0xff, 1, 2, 3};

        var put = browser().send(HttpRequest.newBuilder(uri("/api/artifacts/runs/run-1/easy-acme-amount/step_001.jpg"))
                .header("X-Api-Key", key).header("Content-Type", "image/jpeg")
                .PUT(HttpRequest.BodyPublishers.ofByteArray(jpeg)).build(), HttpResponse.BodyHandlers.discarding());
        assertThat(put.statusCode()).isEqualTo(201);

        String path = "/api/artifacts/" + runId + "/easy-acme-amount/step_001.jpg";
        var get = alice.send(HttpRequest.newBuilder(uri(path)).build(), HttpResponse.BodyHandlers.ofByteArray());
        assertThat(get.statusCode()).isEqualTo(200);
        assertThat(get.body()).isEqualTo(jpeg);
        assertThat(get.headers().firstValue("Content-Type")).hasValue("image/jpeg");

        var asBob = signedIn("bob").send(HttpRequest.newBuilder(uri(path)).build(), HttpResponse.BodyHandlers.discarding());
        assertThat(asBob.statusCode()).isEqualTo(404);
    }

    @Test
    void artifactPathsCannotEscapeStorage() throws Exception {
        HttpClient alice = signedIn("alice");
        String key = createAgent(alice, "invoice-bot");
        gql(browser(), INGEST, runInput("run-1"), key);
        for (String name : new String[] {"..%2F..%2Fsecret.jpg", ".hidden.jpg", "notes.txt"}) {
            var put = browser().send(HttpRequest.newBuilder(uri("/api/artifacts/runs/run-1/easy-acme-amount/" + name))
                    .header("X-Api-Key", key).header("Content-Type", "image/jpeg")
                    .PUT(HttpRequest.BodyPublishers.ofByteArray(new byte[] {1})).build(),
                    HttpResponse.BodyHandlers.discarding());
            assertThat(put.statusCode()).as(name).isIn(400, 404);
        }
    }

    @Test
    void ingestRejectsUnsafeIdentifiers() throws Exception {
        HttpClient alice = signedIn("alice");
        String key = createAgent(alice, "invoice-bot");
        assertThat(errorType(gql(browser(), INGEST, runInput("../etc"), key))).isEqualTo("BAD_REQUEST");
    }

    @Test
    void providersEndpointReportsAvailableSignIn() throws IOException, InterruptedException {
        var resp = browser().send(HttpRequest.newBuilder(uri("/auth/providers")).build(),
                HttpResponse.BodyHandlers.ofString());
        assertThat(resp.body()).contains("\"dev\":true").contains("\"github\":false");
    }
}
