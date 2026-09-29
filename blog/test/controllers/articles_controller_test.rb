require "test_helper"

class ArticlesControllerTest < ActionDispatch::IntegrationTest
  setup do
    @article = articles(:one)
  end

  test "should get index" do
    get articles_url
    assert_response :success
    assert_select "h1", "Articles"
    assert_select "#articles" do
      assert_select "h2", minimum: 1
    end
  end

  test "index returns the first twenty articles in stable order" do
    25.times do |i|
      Article.create!(title: "Page article #{i}", body: "A sufficiently long body for validation.")
    end
    expected_p1 = Article.order(created_at: :desc, id: :desc).limit(20).pluck(:title)
    expected_p2 = Article.order(created_at: :desc, id: :desc).offset(20).limit(20).pluck(:title)

    # 1. Default (app-sliced) pagination
    get articles_url(page: 1)
    assert_response :success
    assert_equal expected_p1, css_select("#articles h2 a").map(&:text)

    get articles_url(page: 2)
    assert_response :success
    assert_equal expected_p2, css_select("#articles h2 a").map(&:text)

    # 2. DB-level pagination (db-paged)
    get articles_url(page: 1, pagination: "db-paged")
    assert_response :success
    assert_equal expected_p1, css_select("#articles h2 a").map(&:text)

    get articles_url(page: 2, pagination: "db-paged")
    assert_response :success
    assert_equal expected_p2, css_select("#articles h2 a").map(&:text)

    # Out of bounds returns empty list
    get articles_url(page: 3, pagination: "db-paged")
    assert_response :success
    assert_equal [], css_select("#articles h2 a").map(&:text)

    # Equivalence: db-paged and app-sliced return identical article sets
    get articles_url(page: 1, pagination: "app-sliced")
    app_sliced_p1 = css_select("#articles h2 a").map(&:text)
    get articles_url(page: 1, pagination: "db-paged")
    db_paged_p1 = css_select("#articles h2 a").map(&:text)
    assert_equal app_sliced_p1, db_paged_p1
  end

  test "index resolves tie-break by id desc when created_at is identical" do
    same_time = Time.zone.parse("2026-01-01 12:00:00")
    a1 = Article.create!(title: "Tie Article 1", body: "Body for article 1.", created_at: same_time)
    a2 = Article.create!(title: "Tie Article 2", body: "Body for article 2.", created_at: same_time)
    a3 = Article.create!(title: "Tie Article 3", body: "Body for article 3.", created_at: same_time)

    # In both db-paged and app-sliced, tie-break created_at desc, id desc puts a3 before a2 before a1
    get articles_url(page: 1, pagination: "db-paged")
    assert_response :success
    titles_db = css_select("#articles h2 a").map(&:text).select { |t| t.start_with?("Tie Article") }
    assert_equal [a3.title, a2.title, a1.title], titles_db

    get articles_url(page: 1, pagination: "app-sliced")
    assert_response :success
    titles_app = css_select("#articles h2 a").map(&:text).select { |t| t.start_with?("Tie Article") }
    assert_equal [a3.title, a2.title, a1.title], titles_app
  end

  test "should get new" do
    get new_article_url
    assert_response :success
    assert_select "form"
  end

  test "should create article" do
    assert_difference("Article.count") do
      post articles_url, params: { article: { body: "A sufficiently long body for validation.", title: "New Title" } }
    end

    assert_redirected_to article_url(Article.last)
    assert_equal "New Title", Article.last.title
  end

  test "should not create article with invalid params" do
    assert_no_difference("Article.count") do
      post articles_url, params: { article: { title: "", body: "" } }
    end

    assert_response :unprocessable_entity
  end

  test "should show article" do
    get article_url(@article)
    assert_response :success
    assert_select "h1", @article.title
    assert_select "h2", "Comments"
    assert_select "#comments .p-4", minimum: 1
  end

  test "should get edit" do
    get edit_article_url(@article)
    assert_response :success
    assert_select "form"
  end

  test "should update article" do
    patch article_url(@article), params: { article: { body: @article.body, title: "Updated Title" } }
    assert_redirected_to article_url(@article)
    @article.reload
    assert_equal "Updated Title", @article.title
  end

  test "should not update article with invalid params" do
    patch article_url(@article), params: { article: { title: "", body: "" } }
    assert_response :unprocessable_entity
  end

  test "should destroy article" do
    assert_difference("Article.count", -1) do
      delete article_url(@article)
    end

    assert_redirected_to articles_url
  end
end
