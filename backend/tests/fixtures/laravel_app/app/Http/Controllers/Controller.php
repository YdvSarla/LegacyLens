<?php

namespace App\Http\Controllers;

abstract class Controller
{
    protected function ok($data)
    {
        return ['ok' => true, 'data' => $data];
    }
}
