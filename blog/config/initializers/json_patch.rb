# frozen_string_literal: true

# Fix compatibility between ActiveSupport 8.1.3.1 and json 3.0+
# ActiveSupport::JSON.decode passes deprecated/removed options to ::JSON.parse,
# raising ArgumentError (wrong number of arguments) in json 3.0.0+.
require "active_support/json"

module ActiveSupport
  module JSON
    def self.decode(json)
      ::JSON.parse(json)
    end
  end
end
