package dev.agentgate.api.graphql;

import dev.agentgate.api.auth.ApiKeys;
import dev.agentgate.api.auth.CurrentUser;
import dev.agentgate.api.auth.Errors;
import dev.agentgate.api.domain.Agent;
import dev.agentgate.api.domain.AgentRepository;
import dev.agentgate.api.domain.AppUser;
import dev.agentgate.api.domain.Run;
import dev.agentgate.api.domain.RunRepository;
import dev.agentgate.api.graphql.Views.CreatedAgent;
import dev.agentgate.api.graphql.Views.TrendPoint;
import java.util.List;
import org.springframework.data.domain.Limit;
import org.springframework.graphql.data.method.annotation.Argument;
import org.springframework.graphql.data.method.annotation.MutationMapping;
import org.springframework.graphql.data.method.annotation.QueryMapping;
import org.springframework.graphql.data.method.annotation.SchemaMapping;
import org.springframework.stereotype.Controller;
import org.springframework.transaction.annotation.Transactional;

@Controller
public class AgentController {

    private static final int MAX_LIMIT = 200;
    private final CurrentUser currentUser;
    private final AgentRepository agents;
    private final RunRepository runs;

    public AgentController(CurrentUser currentUser, AgentRepository agents, RunRepository runs) {
        this.currentUser = currentUser;
        this.agents = agents;
        this.runs = runs;
    }

    @QueryMapping
    public AppUser me() {
        return currentUser.find().orElse(null);
    }

    @QueryMapping
    public List<Agent> agents() {
        return agents.findByOwnerOrderByCreatedAtDesc(currentUser.require());
    }

    @QueryMapping
    public Agent agent(@Argument Long id) {
        return owned(id);
    }

    @MutationMapping
    @Transactional
    public CreatedAgent createAgent(@Argument String name, @Argument String description) {
        AppUser user = currentUser.require();
        String trimmed = name == null ? "" : name.strip();
        if (trimmed.isEmpty() || trimmed.length() > 100) {
            throw new Errors.BadRequest("Name must be 1–100 characters.");
        }
        if (agents.existsByOwnerAndName(user, trimmed)) {
            throw new Errors.BadRequest("You already have an agent called " + trimmed + ".");
        }
        Agent agent = new Agent(user, trimmed, description);
        String key = issueKey(agent);
        return new CreatedAgent(agents.save(agent), key);
    }

    @MutationMapping
    @Transactional
    public CreatedAgent rotateApiKey(@Argument Long agentId) {
        Agent agent = owned(agentId);
        String key = issueKey(agent);  // the old hash is overwritten, so the old key stops working at once
        return new CreatedAgent(agents.save(agent), key);
    }

    @MutationMapping
    @Transactional
    public boolean deleteAgent(@Argument Long agentId) {
        agents.delete(owned(agentId));
        return true;
    }

    @SchemaMapping(typeName = "Agent")
    public List<Run> runs(Agent agent, @Argument Integer limit) {
        return runs.findByAgentOrderByCreatedAtDesc(agent, Limit.of(clamp(limit)));
    }

    @SchemaMapping(typeName = "Agent")
    public Run latestRun(Agent agent) {
        return runs.findByAgentOrderByCreatedAtDesc(agent, Limit.of(1)).stream().findFirst().orElse(null);
    }

    @SchemaMapping(typeName = "Agent")
    public List<TrendPoint> trend(Agent agent, @Argument Integer limit) {
        return runs.findByAgentOrderByCreatedAtDesc(agent, Limit.of(clamp(limit))).reversed().stream()
                .map(r -> new TrendPoint(r.getId(), r.getLabel(), r.getCreatedAt(), r.getSuccessRate(),
                        r.getFieldAccuracy(), r.getP95Seconds(), r.getCostPerTask()))
                .toList();
    }

    private Agent owned(Long id) {
        return agents.findByIdAndOwner(id, currentUser.require())
                .orElseThrow(() -> new Errors.NotFound("Agent not found."));
    }

    private static String issueKey(Agent agent) {
        String key = ApiKeys.generate();
        agent.setApiKey(ApiKeys.prefix(key), ApiKeys.hash(key));
        return key;
    }

    private static int clamp(Integer limit) {
        return Math.max(1, Math.min(limit == null ? 50 : limit, MAX_LIMIT));
    }
}
