package dev.agentgate.api.auth;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.util.Base64;
import java.util.HexFormat;

/**
 * API keys look like "ag_" + 43 random URL-safe characters (256 bits).
 * Only the SHA-256 hash is stored. A fast hash is fine here (unlike passwords)
 * because the key itself has full entropy, so it can't be guessed or brute-forced.
 */
public final class ApiKeys {

    private static final SecureRandom RANDOM = new SecureRandom();
    public static final int PREFIX_LENGTH = 10;

    private ApiKeys() {
    }

    public static String generate() {
        byte[] bytes = new byte[32];
        RANDOM.nextBytes(bytes);
        return "ag_" + Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
    }

    public static String prefix(String key) {
        return key.substring(0, PREFIX_LENGTH);
    }

    public static String hash(String key) {
        try {
            byte[] digest = MessageDigest.getInstance("SHA-256").digest(key.getBytes(StandardCharsets.UTF_8));
            return HexFormat.of().formatHex(digest);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-256 is always available", e);
        }
    }
}
