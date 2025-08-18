import streamlit as st
from snowflake.core import Root # requires snowflake>=0.8.0
from snowflake.snowpark.context import get_active_session
import time

MODELS = [
    "mistral-large",
    "snowflake-arctic",
    "llama3-8b",
]

def init_messages():
    """
    Initialize the session state for chat messages. If the session state indicates that the
    conversation should be cleared or if the "messages" key is not in the session state,
    initialize it as an empty list.
    """
    if st.session_state.clear_conversation or "messages" not in st.session_state:
        st.session_state.messages = []

def init_service_metadata():
    """
    Initialize the session state for cortex search service metadata. Query the available
    cortex search services from the Snowflake session and store their names and search
    columns in the session state.
    """
    if "service_metadata" not in st.session_state:
        services = session.sql("SHOW CORTEX SEARCH SERVICES IN DATABASE MOVIES;").collect()
        service_metadata = []
        if services:
            for s in services:
                svc_name = s["name"]
                svc_search_col = session.sql(
                    f"DESC CORTEX SEARCH SERVICE MOVIES.DATA.{svc_name};"
                ).collect()[0]["search_column"]
                service_metadata.append(
                    {"name": svc_name, "search_column": svc_search_col}
                )

        st.session_state.service_metadata = service_metadata

def init_config_options():
    """
    Initialize the configuration options in the Streamlit sidebar. Allow the user to select
    a cortex search service, clear the conversation, toggle debug mode, and toggle the use of
    chat history. Also provide advanced options to select a model, the number of context chunks,
    and the number of chat messages to use in the chat history.
    """
    st.sidebar.selectbox(
        "Select cortex search service:",
        [s["name"] for s in st.session_state.service_metadata],
        key="selected_cortex_search_service",
    )

    st.sidebar.button("Clear conversation", key="clear_conversation")
    st.sidebar.toggle("Debug", key="debug", value=False)
    st.sidebar.toggle("Use chat history", key="use_chat_history", value=True)

    with st.sidebar.expander("Advanced options"):
        st.selectbox("Select model:", MODELS, key="model_name")
        st.number_input(
            "Select number of messages to use in chat history",
            value=5,
            key="num_chat_messages",
            min_value=1,
            max_value=10,
        )

    st.sidebar.expander("Session State").write(st.session_state)

def query_cortex_search_service(query):
    """
    Query the selected cortex search service with the given query and retrieve context documents.
    Display the retrieved context documents in the sidebar if debug mode is enabled. Return the
    context documents as a string.

    Args:
        query (str): The query to search the cortex search service with.

    Returns:
        str: The concatenated string of context documents.
    """
    db, schema = 'movies', 'data'

    cortex_search_service = (
        root.databases[db]
        .schemas[schema]
        .cortex_search_services[st.session_state.selected_cortex_search_service]
    )

    context_documents = cortex_search_service.search(
        query, columns=[]
    )
    results = context_documents.results

    service_metadata = st.session_state.service_metadata
    search_col = [s["search_column"] for s in service_metadata
                    if s["name"] == st.session_state.selected_cortex_search_service][0]

    context_str = ""
    for i, r in enumerate(results):
        context_str += f"Context document {i+1}: {r[search_col]} \n" + "\n"

    if st.session_state.debug:
        st.sidebar.text_area("Context documents", context_str, height=500)

    return context_str

def get_chat_history():
    """
    Retrieve the chat history from the session state limited to the number of messages specified
    by the user in the sidebar options.

    Returns:
        list: The list of chat messages from the session state.
    """
    start_index = max(
        0, len(st.session_state.messages) - st.session_state.num_chat_messages
    )
    return st.session_state.messages[start_index : len(st.session_state.messages) - 1]

def complete(model, prompt):
    """
    Generate a completion for the given prompt using the specified model.

    Args:
        model (str): The name of the model to use for completion.
        prompt (str): The prompt to generate a completion for.

    Returns:
        str: The generated completion.
    """
    return session.sql("SELECT snowflake.cortex.complete(?,?)", (model, prompt)).collect()[0][0]

def make_chat_history_summary(chat_history, question):
    """
    Generate a summary of the chat history combined with the current question to extend the query
    context. Use the language model to generate this summary.

    Args:
        chat_history (str): The chat history to include in the summary.
        question (str): The current user question to extend with the chat history.

    Returns:
        str: The generated summary of the chat history and question.
    """
    prompt = f"""
        [INST]
        Based on the chat history below and the question, generate a query that extend the question
        with the chat history provided. The query should be in natural language.
        Answer with only the query. Do not add any explanation.

        <chat_history>
        {chat_history}
        </chat_history>
        <question>
        {question}
        </question>
        [/INST]
    """

    summary = complete(st.session_state.model_name, prompt)

    if st.session_state.debug:
        st.sidebar.text_area(
            "Chat history summary", summary.replace("$", "\$"), height=150
        )

    return summary

def create_prompt(user_question):
    """
    Create a prompt for the language model by combining the user question with context retrieved
    from the cortex search service and chat history (if enabled). Format the prompt according to
    the expected input format of the model.

    Args:
        user_question (str): The user's question to generate a prompt for.

    Returns:
        str: The generated prompt for the language model.
    """
    if st.session_state.use_chat_history:
        chat_history = get_chat_history()
        if chat_history != []:
            question_summary = make_chat_history_summary(chat_history, user_question)
            prompt_context = query_cortex_search_service(question_summary)
        else:
            prompt_context = query_cortex_search_service(user_question)
    else:
        prompt_context = query_cortex_search_service(user_question)
        chat_history = ""

    prompt = f"""
            [INST]
            You are a helpful Movie Recommendation chatbot with RAG capabilities. When a user asks you a question about movie recommendations you will recommend movies that are similar to the one they ask about or are in the same genre
            . Use that context with the user's chat history provided in the between <chat_history> and </chat_history> tags
            to provide a summary that addresses the user's question. Ensure the answer is coherent, concise,
            and directly relevant to the user's question.

            If the user asks a generic question which cannot be answered with the given context or chat_history,
            just say "I don't know the answer to that question.

            Don't saying things like "according to the provided context".

            <chat_history>
            {chat_history}
            </chat_history>
            <context>
            {prompt_context}
            </context>
            <question>
            {user_question}
            </question>
            [/INST]
            Answer:
        """
    return prompt

def init_chunking():
    st.markdown("We have not detected a Cortex Service or a chunked table. Please click below to set this up")
    if st.button('Prepare Service'):
        try:
            with st.spinner("Preparing service... please wait."):
                session.sql("BEGIN \
                                call CORTEX_APP_INSTANCE.CORE.TABLE_CHUNKER();\
                                call CORTEX_APP_INSTANCE.CORE.CREATE_CORTEX_SEARCH();\
                            END").collect()

                st.success("Table chunked and Cortex Service created. Reloading...")

                # Reload service metadata
                st.session_state.pop("service_metadata", None)  # Clear old metadata if exists

                # Re-fetch service metadata
                init_service_metadata()
                
                if st.session_state.service_metadata and st.session_state.service_metadata:
                    st.session_state.selected_cortex_search_service = st.session_state.service_metadata[0]["name"]


                # Rerun the app cleanly
                st.rerun()
        except Exception as e:
            st.error(f"An error occurred: {e}")

def ensure_service_ready():
    """
    Ensure that a Cortex Search Service exists and is selected.
    If no service exists, create one and rerun the app.
    """
    init_service_metadata()

    # If no service exists yet, prepare one
    if "service_metadata" not in st.session_state or not st.session_state.service_metadata:
        init_chunking()
        st.stop()

    # If no selection made yet, select the first service
    if "selected_cortex_search_service" not in st.session_state:
        st.session_state.selected_cortex_search_service = st.session_state.service_metadata[0]["name"]


def main():
    st.title(f":movie_camera: Snowflake CineBot")

    ensure_service_ready()
    init_config_options()
    init_messages()

    # If the underlying service has not been created, disable the chat and display config
    disable_chat = (
        "service_metadata" not in st.session_state
        or len(st.session_state.service_metadata) == 0
    )

    if disable_chat:
        init_chunking()

    icons = {"assistant": "❄️", "user": "👤"}

    # Display chat messages from history on app rerun
    for message in st.session_state.messages:
        with st.chat_message(message["role"], avatar=icons[message["role"]]):
            st.markdown(message["content"])

    if question := st.chat_input("Ask a question...", disabled=disable_chat):
        # Add user message to chat history
        st.session_state.messages.append({"role": "user", "content": question})
        # Display user message in chat message container
        with st.chat_message("user", avatar=icons["user"]):
            st.markdown(question.replace("$", "\$"))

        # Display assistant response in chat message container
        with st.chat_message("assistant", avatar=icons["assistant"]):
            message_placeholder = st.empty()
            question = question.replace("'", "")
            with st.spinner("Thinking..."):
                generated_response = complete(
                    st.session_state.model_name, create_prompt(question)
                )
                message_placeholder.markdown(generated_response)

        st.session_state.messages.append(
            {"role": "assistant", "content": generated_response}
        )

if __name__ == "__main__":
    session = get_active_session()
    root = Root(session)
    main()