FROM eclipse-temurin:21-jdk AS build
WORKDIR /src
COPY .mvn .mvn
COPY mvnw pom.xml ./
RUN ./mvnw -q dependency:go-offline
COPY src src
RUN ./mvnw -q package -DskipTests

FROM eclipse-temurin:21-jre
WORKDIR /app
RUN useradd --system --uid 10001 app && mkdir /data && chown app /data
COPY --from=build /src/target/api-*.jar app.jar
USER app
ENV STORAGE_DIR=/data
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "app.jar"]
