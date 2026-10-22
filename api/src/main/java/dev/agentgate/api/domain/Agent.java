package dev.agentgate.api.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.Table;
import java.time.Instant;

@Entity
@Table(name = "agents")
public class Agent {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(optional = false)
    @JoinColumn(name = "owner_id")
    private AppUser owner;

    private String name;
    private String description;

    @Column(name = "api_key_prefix")
    private String apiKeyPrefix;

    @Column(name = "api_key_hash")
    private String apiKeyHash;

    @Column(name = "created_at", insertable = false, updatable = false)
    private Instant createdAt;

    protected Agent() {
    }

    public Agent(AppUser owner, String name, String description) {
        this.owner = owner;
        this.name = name;
        this.description = description;
    }

    public void setApiKey(String prefix, String hash) {
        this.apiKeyPrefix = prefix;
        this.apiKeyHash = hash;
    }

    public boolean isOwnedBy(AppUser user) {
        return owner.getId().equals(user.getId());
    }

    public Long getId() { return id; }
    public AppUser getOwner() { return owner; }
    public String getName() { return name; }
    public String getDescription() { return description; }
    public String getApiKeyPrefix() { return apiKeyPrefix; }
    public Instant getCreatedAt() { return createdAt; }
}
