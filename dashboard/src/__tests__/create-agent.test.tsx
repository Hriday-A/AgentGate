import { MockedProvider } from "@apollo/client/testing/react";
import { fireEvent, render, screen } from "@testing-library/react";
import { CreateAgent } from "@/components/CreateAgent";
import { AGENTS, CREATE_AGENT } from "@/lib/queries";

const mocks = [
  {
    request: { query: CREATE_AGENT, variables: { name: "invoice-bot" } },
    result: { data: { createAgent: { __typename: "CreatedAgent", apiKey: "ag_secret123",
      agent: { __typename: "Agent", id: "1", name: "invoice-bot", apiKeyPrefix: "ag_secret1" } } } },
  },
  { request: { query: AGENTS }, result: { data: { agents: [] } } },  // refetch after creating
];

test("creating an agent reveals its API key once", async () => {
  render(<MockedProvider mocks={mocks}><CreateAgent /></MockedProvider>);
  fireEvent.change(screen.getByLabelText("Agent name"), { target: { value: "invoice-bot" } });
  fireEvent.click(screen.getByRole("button", { name: "Create" }));
  expect(await screen.findByTestId("api-key")).toHaveTextContent("ag_secret123");
  expect(screen.getByRole("status")).toHaveTextContent("won't be shown again");
});
