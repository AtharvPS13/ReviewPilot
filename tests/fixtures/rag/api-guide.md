# API engineering guidelines

## Error response envelope
API handlers must return an error response with an error_code and message.
Use make_error(error_code, message) when building the response.
