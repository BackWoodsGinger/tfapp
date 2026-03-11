$(document).ready(function() {
    // Function to add a new question field
    function addQuestion() {
        var template = $($('#questionTemplate').html());
        template.find('.addChoiceBtn').click(addChoice);
        $('#questions').append(template);
    }

    // Function to add a new choice field to a question
    function addChoice() {
        var template = $($('#choiceTemplate').html());
        $(this).siblings('.choicesContainer').append(template);
    }

    // Attach the function to the button
    $('#add-question').click(addQuestion);

    // Handle the form submission
    $('#survey-form').submit(function(event) {
        event.preventDefault();

        var data = {
            'title': $(this).find('input[name="title"]').val(),
            'description': $(this).find('textarea[name="description"]').val(),
            'questions': []
        };

        $('#questions').children().each(function() {
            var question = {
                'text': $(this).find('input[name="text"]').val(),
                'choices': []
            };

            $(this).find('.choicesContainer').children().each(function() {
                var choice = {
                    'text': $(this).find('input[name="text"]').val()
                };

                question.choices.push(choice);
            });

            data.questions.push(question);
        });

        // Get the CSRF token
        var csrfToken = $('meta[name="csrf-token"]').attr('content');

        $.ajax({
            url: '/api/create_survey/',  // Send data to Django. Update URL as needed.
            type: 'post',
            data: JSON.stringify(data),
            contentType: 'application/json',  // This tells the server to expect JSON
            beforeSend: function(xhr, settings) {
                xhr.setRequestHeader("X-CSRFToken", csrfToken);
            },
            success: function(response) {
                if(response.success) {
                    window.location.href = "/surveys/" + response.survey_id + "/";  // Redirect to the survey's detail page
                }
            },
            error: function(jqXHR, textStatus, errorThrown) {
                // handle error
                console.error(textStatus, errorThrown);
            }
        });
    });
});
