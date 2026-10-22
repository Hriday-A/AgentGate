package dev.agentgate.api.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;

@Entity
@Table(name = "users")
public class AppUser {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "github_id")
    private Long githubId;

    private String login;
    private String name;

    @Column(name = "avatar_url")
    private String avatarUrl;

    @Column(name = "created_at", insertable = false, updatable = false)
    private Instant createdAt;

    protected AppUser() {
    }

    public AppUser(Long githubId, String login, String name, String avatarUrl) {
        this.githubId = githubId;
        this.login = login;
        this.name = name;
        this.avatarUrl = avatarUrl;
    }

    public void updateProfile(String login, String name, String avatarUrl) {
        this.login = login;
        this.name = name;
        this.avatarUrl = avatarUrl;
    }

    public Long getId() { return id; }
    public Long getGithubId() { return githubId; }
    public String getLogin() { return login; }
    public String getName() { return name; }
    public String getAvatarUrl() { return avatarUrl; }
    public Instant getCreatedAt() { return createdAt; }
}
