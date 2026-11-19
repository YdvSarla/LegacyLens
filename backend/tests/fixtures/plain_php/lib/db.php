<?php
$password = "secret-password";

function fetch_rows($page) {
    $conn = mysql_connect('localhost', 'root', 'root');
    $sql = "SELECT * FROM pages WHERE name = '" . $page . "'";
    return mysql_query($sql, $conn);
}
